"""
main.py – Main orchestrator for the Job Notify system.

Pipeline: browser fetch → parse/normalize → diff against state → decide → send → save state.

This module implements the tick/poll architecture:
- Every 30-minute tick: load state, handle Telegram commands, send due reminders,
  send heartbeat if due, save state.
- Portal poll (login + fetch + logout) only when a scheduled poll time is due.
- Notification sending happens AFTER browser is closed.

CLI flags:
  --dry-run           Print notifications without sending; don't write state.
  --test-notify       Send a test notification to every channel.
  --announce-existing Announce existing companies on first run.
  --force-poll        Force a portal poll regardless of schedule.
  --local-debug       Run browser in headed mode (local dev only).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from typing import Any

from . import config
from .models import Company, CompanyChange
from .parser import parse_response, filter_by_program
from .state import (
    load_state, save_state, get_companies, get_notifs,
    mark_notified, is_notified, update_company, prune_expired,
    reset_notif_on_change, is_notified_any_channel,
)
from .rules import diff_companies, get_due_reminders, is_poll_due, is_paused
from .channels import email as email_channel
from .channels import telegram as telegram_channel
from .heartbeat import maybe_send_heartbeat

logger = logging.getLogger(__name__)


def main() -> int:
    """
    Main entry point. Returns 0 on success, 1 on failure.
    """
    args = parse_args()
    setup_logging(args.dry_run)

    logger.info("=== Job Notify Tick ===")

    now = datetime.now(config.IST)
    logger.info("Current time: %s IST", now.strftime("%Y-%m-%d %H:%M:%S"))

    # ── Test-notify mode ──────────────────────────────────────────────────
    if args.test_notify:
        return _handle_test_notify(args)

    # ── Load state ────────────────────────────────────────────────────────
    state = load_state()

    # ── Handle Telegram commands ──────────────────────────────────────────
    if not args.dry_run:
        try:
            telegram_channel.process_commands(state, now)
        except Exception as e:
            logger.warning("Failed to process Telegram commands: %s", e)

    # ── Check pause status ────────────────────────────────────────────────
    paused = is_paused(state)
    if paused:
        logger.info("System is paused until %s", state.get("pause_until"))

    # ── Check if login is stopped ─────────────────────────────────────────
    login_stopped = state.get("login_stopped", False)
    if login_stopped:
        logger.warning("Portal polling stopped due to consecutive login failures. "
                        "Send /resume or run with --force-poll to resume.")

    # ── Determine if poll is due ──────────────────────────────────────────
    last_poll_at = None
    if state.get("last_poll_at"):
        try:
            last_poll_at = datetime.fromisoformat(state["last_poll_at"])
        except (ValueError, TypeError):
            pass

    should_poll = args.force_poll or (
        not login_stopped
        and is_poll_due(now, last_poll_at, paused)
    )

    # ── Portal Poll ───────────────────────────────────────────────────────
    new_companies: dict[int, Company] = {}
    poll_succeeded = False

    if should_poll:
        logger.info("Portal poll starting")
        try:
            new_companies, poll_succeeded = _do_poll(state, args, now)
        except Exception as e:
            logger.error("Poll failed: %s", e)
            _handle_poll_failure(state, e, now, args.dry_run)
    else:
        logger.info("No poll needed this tick")

    # ── Send due reminders (from stored data, no login needed) ────────────
    _send_due_reminders(state, now, args.dry_run)

    # ── Heartbeat ─────────────────────────────────────────────────────────
    if not args.dry_run:
        maybe_send_heartbeat(state, now)

    # ── Prune expired companies ───────────────────────────────────────────
    prune_expired(state, now)

    # ── Update last_success_at ────────────────────────────────────────────
    state["last_success_at"] = now.isoformat()

    # ── Save state ────────────────────────────────────────────────────────
    if not args.dry_run:
        save_state(state)
        logger.info("State saved")
    else:
        logger.info("[DRY RUN] State not saved")

    logger.info("=== Tick complete ===")
    return 0


def _do_poll(
    state: dict[str, Any],
    args: argparse.Namespace,
    now: datetime,
) -> tuple[dict[int, Company], bool]:
    """
    Execute a portal poll: fetch, parse, diff, notify.

    Returns (new_companies_dict, success_bool).
    Notifications are sent AFTER the browser is closed.
    """
    from .portal import fetch_companies, PortalError, LoginFailed

    old_companies = get_companies(state)

    # Fetch from portal (browser opens and closes inside this call)
    try:
        raw_list = fetch_companies(
            headed=args.local_debug,
            previous_company_count=len(old_companies),
        )
    except LoginFailed:
        # Track consecutive login failures
        state["consecutive_login_failures"] = state.get("consecutive_login_failures", 0) + 1
        if state["consecutive_login_failures"] >= config.MAX_LOGIN_FAILURES:
            state["login_stopped"] = True
            alert_msg = (
                f"Login failed {state['consecutive_login_failures']} times consecutively. "
                "Portal polling stopped. Check PORTAL_PASSWORD secret. "
                "Send /resume or run --force-poll after fixing."
            )
            if not args.dry_run:
                _send_alert(alert_msg)
            else:
                logger.info("[DRY RUN] Would send alert: %s", alert_msg)
        raise
    except PortalError:
        raise

    # Reset login failure counter on success
    state["consecutive_login_failures"] = 0
    if state.get("login_stopped"):
        state["login_stopped"] = False

    # Parse and filter
    companies = parse_response({"company_list": raw_list})
    filtered = filter_by_program(companies)

    new_companies: dict[int, Company] = {c.id: c for c in filtered}

    # Diff against state
    changes = diff_companies(old_companies, new_companies)

    # First-run handling
    is_first_run = not state.get("first_run_done", False)
    if is_first_run:
        logger.info("First run detected – %s",
                     "announcing existing" if args.announce_existing else "seeding silently")
        state["first_run_done"] = True

    # Process changes and send notifications
    for change in changes:
        if is_first_run and not args.announce_existing:
            # Seed silently: add to state but don't notify
            update_company(state, change.company)
            # Mark as notified so we don't re-notify later
            mark_notified(state, change.company.id, "new_announced", "email")
            mark_notified(state, change.company.id, "new_announced", "telegram")
            continue

        _process_change(state, change, now, args.dry_run)

    # Update state with all current companies
    for company in filtered:
        update_company(state, company)

    # Record successful poll
    state["last_poll_at"] = now.isoformat()
    state["consecutive_failures"] = 0

    # Recovery notification if we were in alert state
    if state.get("alert_sent"):
        recovery_msg = "Portal polling recovered. System is working normally."
        if not args.dry_run:
            _send_recovery(recovery_msg)
        else:
            logger.info("[DRY RUN] Would send recovery: %s", recovery_msg)
        state["alert_sent"] = False

    return new_companies, True


def _process_change(
    state: dict[str, Any],
    change: CompanyChange,
    now: datetime,
    dry_run: bool,
) -> None:
    """Process a single company change and send notifications."""
    company = change.company

    if change.change_type == "new":
        notif_key = "new_announced"
        if dry_run:
            logger.info("[DRY RUN] NEW: %s (id=%d)", company.company, company.id)
            return

        # Send on each channel independently
        for channel_name, send_fn in [
            ("email", lambda: email_channel.send_new_company(company)),
            ("telegram", lambda: telegram_channel.send_new_company(company)),
        ]:
            if not is_notified(state, company.id, notif_key, channel_name):
                if send_fn():
                    mark_notified(state, company.id, notif_key, channel_name)

    elif change.change_type == "start_changed":
        notif_key = "start_changed"
        # Reset reminder tracking when start changes
        reset_notif_on_change(state, company.id, "registration_open")

        if dry_run:
            logger.info("[DRY RUN] START CHANGED: %s – %s → %s",
                        company.company, change.old_value, change.new_value)
            return

        for channel_name, send_fn in [
            ("email", lambda: email_channel.send_change(change)),
            ("telegram", lambda: telegram_channel.send_change(change)),
        ]:
            if not is_notified(state, company.id, notif_key, channel_name):
                if send_fn():
                    mark_notified(state, company.id, notif_key, channel_name)

    elif change.change_type == "deadline_changed":
        notif_key = "deadline_changed"
        # Reset reminder tracking when deadline changes
        reset_notif_on_change(state, company.id, "reminder_24h")
        reset_notif_on_change(state, company.id, "reminder_2h")

        if dry_run:
            logger.info("[DRY RUN] DEADLINE CHANGED: %s – %s → %s",
                        company.company, change.old_value, change.new_value)
            return

        for channel_name, send_fn in [
            ("email", lambda: email_channel.send_change(change)),
            ("telegram", lambda: telegram_channel.send_change(change)),
        ]:
            if not is_notified(state, company.id, notif_key, channel_name):
                if send_fn():
                    mark_notified(state, company.id, notif_key, channel_name)


def _send_due_reminders(
    state: dict[str, Any],
    now: datetime,
    dry_run: bool,
) -> None:
    """
    Check all companies in state for due reminders and send them.

    This runs on EVERY tick, not just poll ticks, using stored data.
    Reminders fire even when paused.
    """
    companies = get_companies(state)

    for cid, company in companies.items():
        notifs = get_notifs(state, cid)
        due = get_due_reminders(company, now, notifs)

        for notif_key in due:
            if dry_run:
                logger.info("[DRY RUN] REMINDER %s: %s (id=%d)",
                            notif_key, company.company, cid)
                continue

            for channel_name, send_fn in _reminder_senders(company, notif_key):
                if not is_notified(state, cid, notif_key, channel_name):
                    if send_fn():
                        mark_notified(state, cid, notif_key, channel_name)


def _reminder_senders(company: Company, notif_key: str):
    """Get the send functions for each channel for a reminder type."""
    if notif_key == "registration_open":
        return [
            ("email", lambda: email_channel.send_registration_open(company)),
            ("telegram", lambda: telegram_channel.send_registration_open(company)),
        ]
    else:
        return [
            ("email", lambda: email_channel.send_reminder(company, notif_key)),
            ("telegram", lambda: telegram_channel.send_reminder(company, notif_key)),
        ]


def _handle_poll_failure(
    state: dict[str, Any],
    error: Exception,
    now: datetime,
    dry_run: bool,
) -> None:
    """Handle a failed poll: track failures, alert if threshold reached."""
    state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
    failures = state["consecutive_failures"]

    failure_class = "UNKNOWN"
    if hasattr(error, "failure_class"):
        failure_class = error.failure_class

    logger.error("Poll failure #%d, class=%s: %s", failures, failure_class, error)

    # Send alert on third consecutive failure
    if failures >= config.CONSECUTIVE_FAILURES_ALERT and not state.get("alert_sent"):
        alert_msg = (
            f"Portal scraper has failed {failures} times consecutively.\n"
            f"Failure class: {failure_class}\n"
            "Please check the workflow logs."
        )
        if not dry_run:
            _send_alert(alert_msg)
            state["alert_sent"] = True
        else:
            logger.info("[DRY RUN] Would send alert: %s", alert_msg)


def _send_alert(message: str) -> None:
    """Send alert on all channels."""
    email_channel.send_alert(message)
    telegram_channel.send_alert(message)


def _send_recovery(message: str) -> None:
    """Send recovery on all channels."""
    email_channel.send_recovery(message)
    telegram_channel.send_recovery(message)


def _handle_test_notify(args: argparse.Namespace) -> int:
    """Handle --test-notify flag."""
    logger.info("Sending test notifications...")

    email_ok = email_channel.send_test()
    telegram_ok = telegram_channel.send_test()

    if email_ok:
        logger.info("✅ Email test: SUCCESS")
    else:
        logger.error("❌ Email test: FAILED")

    if telegram_ok:
        logger.info("✅ Telegram test: SUCCESS")
    else:
        logger.error("❌ Telegram test: FAILED")

    if email_ok and telegram_ok:
        logger.info("All channels working!")
        return 0
    else:
        logger.error("Some channels failed")
        return 1


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Job Notify – College Placement Portal Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print notifications without sending; don't write state.",
    )
    parser.add_argument(
        "--test-notify",
        action="store_true",
        help="Send a test notification to every channel.",
    )
    parser.add_argument(
        "--announce-existing",
        action="store_true",
        help="Announce existing companies on first run.",
    )
    parser.add_argument(
        "--force-poll",
        action="store_true",
        help="Force a portal poll regardless of schedule.",
    )
    parser.add_argument(
        "--local-debug",
        action="store_true",
        help="Run browser in headed mode for local debugging.",
    )
    return parser.parse_args()


def setup_logging(dry_run: bool = False) -> None:
    """Configure logging with secret masking."""
    level = logging.DEBUG if dry_run else logging.INFO

    # Custom formatter that masks secrets
    class SecretMaskingFormatter(logging.Formatter):
        """Replaces any leaked secrets in log output with [REDACTED]."""
        def format(self, record):
            msg = super().format(record)
            # Mask any secrets that might leak
            secrets_to_mask = [
                config.PORTAL_USERNAME,
                config.PORTAL_PASSWORD,
                config.TELEGRAM_BOT_TOKEN,
                config.GMAIL_APP_PASSWORD,
            ]
            for secret in secrets_to_mask:
                if secret and len(secret) > 3:
                    msg = msg.replace(secret, "[REDACTED]")
            return msg

    handler = logging.StreamHandler()
    handler.setFormatter(SecretMaskingFormatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)


# ── Entry point ───────────────────────────────────────────────────────────────

def run() -> None:
    """Entry point for `python -m notifier.main`."""
    sys.exit(main())


if __name__ == "__main__":
    run()
