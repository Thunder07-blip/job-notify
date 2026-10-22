"""
telegram.py – Telegram notification channel and command handler.

Uses the Telegram Bot API via urllib.request (no external dependencies).
All dynamic text is HTML-escaped to prevent injection.
Handles /pause, /resume, /status commands via getUpdates.
"""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from typing import Any, Optional

from .. import config
from ..models import Company, CompanyChange

logger = logging.getLogger(__name__)


# ── Sending Messages ──────────────────────────────────────────────────────────

def send_new_company(company: Company) -> bool:
    """Send notification for a newly detected company."""
    text = (
        f"🏢 <b>New Company</b>\n\n"
        f"<b>{_esc(company.company)}</b>\n"
        f"Type: {_esc(company.placementtype)}\n"
        f"Package: {_esc(company.package_display())}\n"
        f"Programs: {_esc(company.tpoprogram)}\n"
        f"Start: {_esc(_fmt_dt(company.form_start))}\n"
        f"Deadline: {_esc(_fmt_dt(company.deadline))}\n"
        f"Year: {_esc(company.academicyear)}\n\n"
        f"🔗 <a href=\"https://tpo.vierp.in/company-dashboard\">Register Now</a>"
    )
    return _send_message(text)


def send_change(change: CompanyChange) -> bool:
    """Send notification for a changed start time or deadline."""
    c = change.company
    field = "Start Time" if change.change_type == "start_changed" else "Deadline"
    text = (
        f"📝 <b>Update: {_esc(c.company)}</b>\n\n"
        f"<b>{field} Changed</b>\n"
        f"Before: <s>{_esc(change.old_value or 'Not set')}</s>\n"
        f"After: <b>{_esc(change.new_value or 'Not set')}</b>\n\n"
        f"🔗 <a href=\"https://tpo.vierp.in/company-dashboard\">Check Portal</a>"
    )
    return _send_message(text)


def send_reminder(company: Company, reminder_type: str) -> bool:
    """Send a deadline reminder."""
    if reminder_type == "reminder_24h":
        emoji, label = "⏰", "24 HOURS"
    elif reminder_type == "reminder_2h":
        emoji, label = "🚨", "2 HOURS"
    else:
        emoji, label = "⏰", reminder_type

    text = (
        f"{emoji} <b>Deadline in {label}!</b>\n\n"
        f"<b>{_esc(company.company)}</b>\n"
        f"Deadline: {_esc(_fmt_dt(company.deadline))}\n"
        f"Type: {_esc(company.placementtype)}\n\n"
        f"🔗 <a href=\"https://tpo.vierp.in/company-dashboard\">Register NOW</a>"
    )
    return _send_message(text)


def send_registration_open(company: Company) -> bool:
    """Send notification that registration is now open."""
    text = (
        f"✅ <b>Registration OPEN!</b>\n\n"
        f"<b>{_esc(company.company)}</b>\n"
        f"Type: {_esc(company.placementtype)}\n"
        f"Package: {_esc(company.package_display())}\n"
        f"Deadline: {_esc(_fmt_dt(company.deadline))}\n\n"
        f"🔗 <a href=\"https://tpo.vierp.in/company-dashboard\">Register Now</a>"
    )
    return _send_message(text)


def send_alert(message: str) -> bool:
    """Send an alert (scraper broken, login failed)."""
    text = f"⚠️ <b>System Alert</b>\n\n{_esc(message)}"
    return _send_message(text)


def send_recovery(message: str) -> bool:
    """Send a recovery notification."""
    text = f"✅ <b>System Recovered</b>\n\n{_esc(message)}"
    return _send_message(text)


def send_heartbeat(message: str) -> bool:
    """Send a weekly heartbeat."""
    text = f"💓 <b>Heartbeat</b>\n\n{_esc(message)}"
    return _send_message(text)


def send_test() -> bool:
    """Send a test notification."""
    text = "🧪 <b>Test Notification</b>\n\nEmail and Telegram are configured correctly!"
    return _send_message(text)


# ── Command Handling ──────────────────────────────────────────────────────────

def process_commands(state: dict[str, Any], now: datetime) -> None:
    """
    Fetch and process Telegram commands from getUpdates.

    Only processes messages from the configured TELEGRAM_CHAT_ID.
    Handles /pause, /resume, /status.
    Tracks the update offset in state so each command is processed once.
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return

    offset = state.get("telegram_offset", 0)
    updates = _get_updates(offset)

    if not updates:
        return

    for update in updates:
        update_id = update.get("update_id", 0)
        message = update.get("message", {})

        # Always advance the offset
        if update_id >= offset:
            state["telegram_offset"] = update_id + 1

        # Only process messages from our chat
        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            logger.debug("Ignoring message from chat %s (expected %s)",
                         chat_id, config.TELEGRAM_CHAT_ID)
            continue

        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            continue

        parts = text.split()
        command = parts[0].lower()

        if command == "/pause":
            _handle_pause(state, parts, now)
        elif command == "/resume":
            _handle_resume(state)
        elif command == "/status":
            _handle_status(state, now)
        else:
            logger.debug("Unknown command: %s", command)


def _handle_pause(state: dict, parts: list[str], now: datetime) -> None:
    """Handle /pause [hours] command. Default 2h, max 12h."""
    hours = 2
    if len(parts) > 1:
        try:
            hours = min(int(parts[1]), 12)
            hours = max(hours, 1)
        except ValueError:
            hours = 2

    pause_until = now + timedelta(hours=hours)
    state["pause_until"] = pause_until.isoformat()
    _send_message(f"⏸️ Paused for {hours} hours (until {pause_until.strftime('%H:%M IST')}).\nSend /resume to unpause.")
    logger.info("Paused until %s", pause_until.isoformat())


def _handle_resume(state: dict) -> None:
    """Handle /resume command."""
    state["pause_until"] = None
    state["login_stopped"] = False  # also clear login-stop on resume
    _send_message("▶️ Resumed! Portal polling is active again.")
    logger.info("Resumed from pause")


def _handle_status(state: dict, now: datetime) -> None:
    """Handle /status command."""
    from ..rules import next_poll_time, is_paused

    last_poll = state.get("last_poll_at", "Never")
    next_poll = next_poll_time(now)
    next_poll_str = next_poll.strftime("%H:%M IST") if next_poll else "Unknown"
    company_count = len(state.get("companies", {}))
    paused = is_paused(state)
    pause_info = ""
    if paused:
        pause_until = state.get("pause_until", "")
        pause_info = f"\n⏸️ Paused until {pause_until}"

    login_stopped = state.get("login_stopped", False)
    login_info = ""
    if login_stopped:
        login_info = "\n🚫 Polling stopped due to login failures"

    text = (
        f"📊 <b>Status</b>\n\n"
        f"Last poll: {_esc(str(last_poll))}\n"
        f"Next poll: {_esc(next_poll_str)}\n"
        f"Active companies: {company_count}\n"
        f"Failures: {state.get('consecutive_failures', 0)}"
        f"{pause_info}{login_info}"
    )
    _send_message(text)


# ── Low-level API calls ──────────────────────────────────────────────────────

def _send_message(text: str) -> bool:
    """Send a message via Telegram Bot API."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.error("Telegram not configured: missing BOT_TOKEN or CHAT_ID")
        return False

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                logger.info("Telegram message sent successfully")
                return True
            else:
                logger.error("Telegram API error: %s", result.get("description", "unknown"))
                return False
    except Exception as e:
        logger.error("Failed to send Telegram message: %s", e)
        return False


def _get_updates(offset: int) -> list[dict]:
    """Fetch new updates from the Telegram Bot API."""
    if not config.TELEGRAM_BOT_TOKEN:
        return []

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates"
    payload = json.dumps({
        "offset": offset,
        "timeout": 1,
        "allowed_updates": ["message"],
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                return result.get("result", [])
            else:
                logger.warning("getUpdates error: %s", result.get("description", "unknown"))
                return []
    except Exception as e:
        logger.warning("Failed to get Telegram updates: %s", e)
        return []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    """Escape HTML special characters for Telegram HTML parse mode."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _fmt_dt(dt: Optional[datetime]) -> str:
    """Format a datetime for display."""
    if dt is None:
        return "Not set"
    return dt.strftime("%d %b %Y %H:%M IST")
