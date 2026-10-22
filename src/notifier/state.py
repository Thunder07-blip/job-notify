"""
state.py – Atomic, versioned state management.

State lives in state/state.json and is committed back by the GitHub Actions workflow.
This module provides:
- Atomic writes (write to temp file, then os.rename).
- Schema versioning with migration support.
- Validation on load.
- Per-company tracking of sent notifications per channel.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import config
from .models import Company

logger = logging.getLogger(__name__)

# Notification keys tracked per channel
NOTIF_KEYS = {
    "new_announced",           # initial announcement sent
    "start_changed",           # form_start change notified
    "deadline_changed",        # deadline change notified
    "registration_open",       # registration-opens notification
    "reminder_24h",            # 24-hour deadline reminder
    "reminder_2h",             # 2-hour deadline reminder
}

CHANNELS = {"email", "telegram"}


def default_state() -> dict[str, Any]:
    """Return a fresh, empty state structure."""
    return {
        "schema_version": config.CURRENT_SCHEMA_VERSION,
        "companies": {},                   # {str(id): company_dict_with_notifs}
        "last_poll_at": None,              # ISO timestamp of last successful poll
        "last_success_at": None,           # ISO timestamp of last successful tick
        "last_heartbeat_at": None,         # ISO timestamp of last heartbeat
        "consecutive_failures": 0,
        "consecutive_login_failures": 0,
        "alert_sent": False,               # "scraper broken" alert sent
        "login_stopped": False,            # stopped polling due to login failures
        "pause_until": None,               # ISO timestamp when pause expires
        "telegram_offset": 0,              # getUpdates offset for command tracking
        "first_run_done": False,           # whether first-run seeding has happened
    }


def load_state(path: Optional[str] = None) -> dict[str, Any]:
    """
    Load state from disk. Returns default state if file doesn't exist
    or is empty/corrupt.
    """
    path = path or config.STATE_FILE
    filepath = Path(path)

    if not filepath.exists():
        logger.info("State file not found at %s, starting with default state", path)
        return default_state()

    try:
        text = filepath.read_text(encoding="utf-8").strip()
        if not text:
            logger.info("State file is empty, starting with default state")
            return default_state()

        data = json.loads(text)
        if not isinstance(data, dict):
            logger.warning("State file is not a JSON object, resetting")
            return default_state()

        # Validate and migrate
        data = _migrate(data)
        data = _validate(data)
        return data

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error("Failed to load state from %s: %s. Starting fresh.", path, e)
        return default_state()


def save_state(state: dict[str, Any], path: Optional[str] = None) -> None:
    """
    Atomically save state to disk.

    Writes to a temporary file in the same directory, then renames.
    This prevents corruption from interrupted writes.
    """
    path = path or config.STATE_FILE
    filepath = Path(path)

    # Ensure parent directory exists
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file, then atomic rename
    fd, tmp_path = tempfile.mkstemp(
        dir=str(filepath.parent),
        prefix=".state_",
        suffix=".json.tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, str(filepath))
        logger.debug("State saved to %s", path)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_companies(state: dict[str, Any]) -> dict[int, Company]:
    """Extract Company objects from state, keyed by ID."""
    result: dict[int, Company] = {}
    for id_str, data in state.get("companies", {}).items():
        try:
            company = Company.from_dict(data)
            result[company.id] = company
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("Skipping corrupt company in state id=%s: %s", id_str, e)
    return result


def get_notifs(state: dict[str, Any], company_id: int) -> dict[str, set[str]]:
    """
    Get the notification tracking dict for a company.

    Returns: {notif_key: set_of_channels_that_succeeded}
    """
    companies = state.get("companies", {})
    company_data = companies.get(str(company_id), {})
    notifs_raw = company_data.get("notifs", {})

    result: dict[str, set[str]] = {}
    for key in NOTIF_KEYS:
        channels = notifs_raw.get(key, [])
        result[key] = set(channels) if isinstance(channels, list) else set()
    return result


def mark_notified(
    state: dict[str, Any],
    company_id: int,
    notif_key: str,
    channel: str,
) -> None:
    """Mark a notification as sent for a specific company and channel."""
    id_str = str(company_id)
    companies = state.setdefault("companies", {})
    company_data = companies.get(id_str)
    if company_data is None:
        return  # company not in state, nothing to mark

    notifs = company_data.setdefault("notifs", {})
    channels = notifs.setdefault(notif_key, [])
    if channel not in channels:
        channels.append(channel)


def is_notified(
    state: dict[str, Any],
    company_id: int,
    notif_key: str,
    channel: str,
) -> bool:
    """Check if a notification has already been sent for a company on a channel."""
    notifs = get_notifs(state, company_id)
    return channel in notifs.get(notif_key, set())


def is_notified_any_channel(
    state: dict[str, Any],
    company_id: int,
    notif_key: str,
) -> bool:
    """Check if a notification has been sent on at least one channel."""
    notifs = get_notifs(state, company_id)
    return len(notifs.get(notif_key, set())) > 0


def update_company(state: dict[str, Any], company: Company) -> None:
    """
    Update or add a company in state, preserving notification tracking.
    """
    id_str = str(company.id)
    companies = state.setdefault("companies", {})

    # Preserve existing notification tracking
    existing = companies.get(id_str, {})
    existing_notifs = existing.get("notifs", {})

    company_data = company.to_dict()
    company_data["notifs"] = existing_notifs
    companies[id_str] = company_data


def remove_company(state: dict[str, Any], company_id: int) -> None:
    """Remove a company from state."""
    state.get("companies", {}).pop(str(company_id), None)


def prune_expired(state: dict[str, Any], now: datetime) -> int:
    """
    Remove companies whose deadline passed more than PRUNE_AFTER_DAYS ago.

    Returns the number of pruned companies.
    """
    from datetime import timedelta
    cutoff = now - timedelta(days=config.PRUNE_AFTER_DAYS)
    companies = state.get("companies", {})
    to_remove = []

    for id_str, data in companies.items():
        deadline_str = data.get("deadline")
        if deadline_str:
            try:
                deadline = datetime.fromisoformat(deadline_str)
                if deadline < cutoff:
                    to_remove.append(id_str)
            except (ValueError, TypeError):
                pass

    for id_str in to_remove:
        del companies[id_str]

    if to_remove:
        logger.info("Pruned %d expired companies", len(to_remove))
    return len(to_remove)


def reset_notif_on_change(
    state: dict[str, Any],
    company_id: int,
    notif_key: str,
) -> None:
    """
    Reset a notification key for a company (e.g., when a deadline changes,
    reset reminder tracking so new reminders are sent for the new deadline).
    """
    id_str = str(company_id)
    companies = state.get("companies", {})
    company_data = companies.get(id_str, {})
    notifs = company_data.get("notifs", {})
    if notif_key in notifs:
        del notifs[notif_key]


def _migrate(data: dict[str, Any]) -> dict[str, Any]:
    """
    Migrate state from older schema versions to the current version.
    """
    version = data.get("schema_version", 0)

    if version < 1:
        # Migration from v0 (pre-versioned) to v1
        data.setdefault("schema_version", 1)
        data.setdefault("companies", {})
        data.setdefault("last_poll_at", None)
        data.setdefault("last_success_at", None)
        data.setdefault("last_heartbeat_at", None)
        data.setdefault("consecutive_failures", 0)
        data.setdefault("consecutive_login_failures", 0)
        data.setdefault("alert_sent", False)
        data.setdefault("login_stopped", False)
        data.setdefault("pause_until", None)
        data.setdefault("telegram_offset", 0)
        data.setdefault("first_run_done", False)
        logger.info("Migrated state from v%d to v1", version)

    data["schema_version"] = config.CURRENT_SCHEMA_VERSION
    return data


def _validate(data: dict[str, Any]) -> dict[str, Any]:
    """Ensure all required keys exist with correct types."""
    defaults = default_state()
    for key, default_value in defaults.items():
        if key not in data:
            data[key] = default_value
        elif key == "companies" and not isinstance(data[key], dict):
            data[key] = {}

    # Ensure consecutive_failures is an int
    try:
        data["consecutive_failures"] = int(data["consecutive_failures"])
    except (ValueError, TypeError):
        data["consecutive_failures"] = 0

    try:
        data["consecutive_login_failures"] = int(data["consecutive_login_failures"])
    except (ValueError, TypeError):
        data["consecutive_login_failures"] = 0

    try:
        data["telegram_offset"] = int(data["telegram_offset"])
    except (ValueError, TypeError):
        data["telegram_offset"] = 0

    return data
