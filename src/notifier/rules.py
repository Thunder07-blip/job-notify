"""
rules.py – Business logic for detecting changes and deciding notifications.

This module implements the diff engine and reminder scheduling:
- Detect NEW companies.
- Detect CHANGED form_start or deadline (including None → value).
- REGISTRATION OPENS: now >= form_start.
- DEADLINE REMINDERS: 24h and 2h before deadline.
- First-run seeding (silent by default).
- Poll scheduling (tick vs poll, POLL_TIMES_IST).
- Pause/resume logic.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from . import config
from .models import Company, CompanyChange

logger = logging.getLogger(__name__)


def diff_companies(
    old: dict[int, Company],
    new: dict[int, Company],
) -> list[CompanyChange]:
    """
    Compare old and new company snapshots and produce a list of changes.

    Detects:
    - New companies (in new but not in old).
    - Changed form_start (different value or None → value).
    - Changed deadline (different value or None → value).

    Does NOT detect removed companies – removal is handled by
    EMPTY_RESULT_SUSPICIOUS guard in portal.py.
    """
    changes: list[CompanyChange] = []

    for cid, new_company in new.items():
        if cid not in old:
            changes.append(CompanyChange(
                company=new_company,
                change_type="new",
            ))
            continue

        old_company = old[cid]

        # Check form_start change
        if new_company.form_start != old_company.form_start:
            changes.append(CompanyChange(
                company=new_company,
                change_type="start_changed",
                old_value=_fmt_dt(old_company.form_start),
                new_value=_fmt_dt(new_company.form_start),
            ))

        # Check deadline change
        if new_company.deadline != old_company.deadline:
            changes.append(CompanyChange(
                company=new_company,
                change_type="deadline_changed",
                old_value=_fmt_dt(old_company.deadline),
                new_value=_fmt_dt(new_company.deadline),
            ))

    return changes


def get_due_reminders(
    company: Company,
    now: datetime,
    notified_keys: dict[str, set[str]],
) -> list[str]:
    """
    Determine which reminders are due for a company.

    Returns a list of notification keys (e.g., "registration_open",
    "reminder_24h", "reminder_2h") that should be sent.

    Rules:
    - registration_open: once when now >= form_start.
    - reminder_24h: once when deadline is within 24 hours.
    - reminder_2h: once when deadline is within 2 hours.
    - Never remind after the deadline.
    - If first seen inside a reminder window, send only the most urgent.
    """
    due: list[str] = []

    # Registration opens reminder
    if company.form_start and now >= company.form_start:
        if not notified_keys.get("registration_open"):
            due.append("registration_open")

    # Deadline reminders – only if deadline is in the future
    if company.deadline and now < company.deadline:
        time_until = (company.deadline - now).total_seconds()

        within_24h = time_until <= config.REMINDER_24H_SECONDS
        within_2h = time_until <= config.REMINDER_2H_SECONDS

        already_24h = bool(notified_keys.get("reminder_24h"))
        already_2h = bool(notified_keys.get("reminder_2h"))

        if within_2h and not already_2h:
            # Most urgent – always send 2h reminder
            due.append("reminder_2h")
            # If we haven't sent 24h either, DON'T send it now (only most urgent)
        elif within_24h and not already_24h:
            due.append("reminder_24h")

    return due


def is_poll_due(
    now: datetime,
    last_poll_at: Optional[datetime],
    paused: bool,
) -> bool:
    """
    Determine if a portal poll should happen on this tick.

    A poll is due when:
    1. Not paused.
    2. The most recent scheduled time (from POLL_TIMES_IST) has passed
       since last_poll_at.
    3. The poll hasn't already happened for that slot.
    4. Within the grace window.

    Uses IST times for schedule comparison.
    """
    if paused:
        logger.info("Poll skipped: system is paused")
        return False

    now_ist = now.astimezone(config.IST)
    today = now_ist.date()

    # Find the most recent scheduled poll time
    latest_slot = _find_latest_slot(now_ist, today)
    if latest_slot is None:
        logger.debug("No poll slot applies at %s IST", now_ist.strftime("%H:%M"))
        return False

    # Check if already polled for this slot
    if last_poll_at:
        last_poll_ist = last_poll_at.astimezone(config.IST)
        if last_poll_ist >= latest_slot:
            logger.debug("Already polled for slot %s", latest_slot.strftime("%H:%M"))
            return False

    # Check grace window: don't poll if we're too far past the slot
    seconds_since_slot = (now_ist - latest_slot).total_seconds()
    if seconds_since_slot > config.POLL_GRACE_WINDOW_SECONDS:
        logger.debug("Slot %s is outside grace window (%d > %d seconds)",
                      latest_slot.strftime("%H:%M"), seconds_since_slot,
                      config.POLL_GRACE_WINDOW_SECONDS)
        return False

    logger.info("Poll is due for slot %s", latest_slot.strftime("%H:%M"))
    return True


def _find_latest_slot(now_ist: datetime, today) -> Optional[datetime]:
    """
    Find the most recent scheduled poll time that is <= now.

    Checks today's slots and yesterday's last slot (for overnight grace window).
    Returns a timezone-aware datetime or None.
    """
    slots_today = []
    for time_str in config.POLL_TIMES_IST:
        try:
            h, m = map(int, time_str.split(":"))
            slot = datetime(today.year, today.month, today.day, h, m,
                            tzinfo=config.IST)
            if slot <= now_ist:
                slots_today.append(slot)
        except (ValueError, IndexError):
            logger.warning("Invalid poll time: %s", time_str)

    if slots_today:
        return max(slots_today)

    # Check yesterday's last slot (for runs right after midnight)
    yesterday = today - timedelta(days=1)
    slots_yesterday = []
    for time_str in config.POLL_TIMES_IST:
        try:
            h, m = map(int, time_str.split(":"))
            slot = datetime(yesterday.year, yesterday.month, yesterday.day, h, m,
                            tzinfo=config.IST)
            slots_yesterday.append(slot)
        except (ValueError, IndexError):
            pass

    if slots_yesterday:
        last_yesterday = max(slots_yesterday)
        # Only if within grace window
        if (now_ist - last_yesterday).total_seconds() <= config.POLL_GRACE_WINDOW_SECONDS:
            return last_yesterday

    return None


def is_paused(state: dict, now: Optional[datetime] = None) -> bool:
    """Check if the system is currently paused."""
    pause_until = state.get("pause_until")
    if not pause_until:
        return False
    try:
        pause_dt = datetime.fromisoformat(pause_until)
        current = now if now is not None else datetime.now(config.IST)
        return current < pause_dt
    except (ValueError, TypeError):
        return False


def is_heartbeat_due(state: dict, now: datetime) -> bool:
    """Check if a heartbeat notification is due."""
    last = state.get("last_heartbeat_at")
    if not last:
        return True  # never sent
    try:
        last_dt = datetime.fromisoformat(last)
        hours_since = (now - last_dt).total_seconds() / 3600
        return hours_since >= config.HEARTBEAT_INTERVAL_HOURS
    except (ValueError, TypeError):
        return True


def next_poll_time(now: datetime) -> Optional[datetime]:
    """Calculate the next scheduled poll time for display purposes."""
    now_ist = now.astimezone(config.IST)
    today = now_ist.date()

    # Check remaining slots today
    for time_str in sorted(config.POLL_TIMES_IST):
        try:
            h, m = map(int, time_str.split(":"))
            slot = datetime(today.year, today.month, today.day, h, m,
                            tzinfo=config.IST)
            if slot > now_ist:
                return slot
        except (ValueError, IndexError):
            pass

    # Next day's first slot
    tomorrow = today + timedelta(days=1)
    for time_str in sorted(config.POLL_TIMES_IST):
        try:
            h, m = map(int, time_str.split(":"))
            return datetime(tomorrow.year, tomorrow.month, tomorrow.day, h, m,
                            tzinfo=config.IST)
        except (ValueError, IndexError):
            pass

    return None


def _fmt_dt(dt: Optional[datetime]) -> str:
    """Format a datetime for human display, or 'Not set'."""
    if dt is None:
        return "Not set"
    return dt.strftime("%d %b %Y %H:%M IST")
