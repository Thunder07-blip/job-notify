"""
test_state.py – Tests for state management.

Covers:
- Atomic writes (temp file + rename).
- Load/save round-trip.
- Schema migration.
- Validation on load.
- Notification tracking per-channel.
- Pruning expired companies.
- First-run seeding.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from notifier.state import (
    load_state, save_state, default_state, get_companies,
    get_notifs, mark_notified, is_notified, is_notified_any_channel,
    update_company, remove_company, prune_expired,
    reset_notif_on_change,
)
from notifier.models import Company

IST = ZoneInfo("Asia/Kolkata")


class TestLoadSave:
    """Tests for state persistence."""

    def test_save_and_load(self, tmp_state_file):
        """Save + load round-trip produces equivalent state."""
        state = default_state()
        state["consecutive_failures"] = 5
        save_state(state, tmp_state_file)
        loaded = load_state(tmp_state_file)
        assert loaded["consecutive_failures"] == 5
        assert loaded["schema_version"] == 1

    def test_load_missing_file(self, tmp_path):
        """Loading from a nonexistent file returns default state."""
        state = load_state(str(tmp_path / "nonexistent.json"))
        assert state == default_state()

    def test_load_empty_file(self, tmp_state_file):
        """Loading an empty file returns default state."""
        Path(tmp_state_file).write_text("")
        state = load_state(tmp_state_file)
        assert state == default_state()

    def test_load_corrupt_json(self, tmp_state_file):
        """Loading corrupt JSON returns default state."""
        Path(tmp_state_file).write_text("{bad json")
        state = load_state(tmp_state_file)
        assert state == default_state()

    def test_load_non_object(self, tmp_state_file):
        """Loading a JSON array returns default state."""
        Path(tmp_state_file).write_text("[1, 2, 3]")
        state = load_state(tmp_state_file)
        assert state == default_state()

    def test_atomic_write(self, tmp_state_file):
        """No temp files are left behind after save."""
        state = default_state()
        save_state(state, tmp_state_file)
        parent = Path(tmp_state_file).parent
        temps = list(parent.glob(".state_*"))
        assert len(temps) == 0

    def test_creates_parent_dirs(self, tmp_path):
        """save_state creates parent directories if needed."""
        path = str(tmp_path / "deep" / "nested" / "state.json")
        save_state(default_state(), path)
        assert Path(path).exists()


class TestMigration:
    """Tests for schema migration."""

    def test_migrate_v0_to_v1(self, tmp_state_file):
        """A v0 state (no schema_version) gets migrated to v1."""
        Path(tmp_state_file).write_text(json.dumps({"companies": {}}))
        state = load_state(tmp_state_file)
        assert state["schema_version"] == 1
        assert "telegram_offset" in state
        assert "first_run_done" in state

    def test_missing_keys_filled(self, tmp_state_file):
        """Missing keys are filled with defaults during validation."""
        Path(tmp_state_file).write_text(json.dumps({"schema_version": 1}))
        state = load_state(tmp_state_file)
        assert "companies" in state
        assert "pause_until" in state


class TestNotificationTracking:
    """Tests for per-channel notification tracking."""

    def test_mark_and_check(self, clean_state):
        """Mark a notification and verify it's tracked."""
        c = _make_company(1)
        update_company(clean_state, c)
        mark_notified(clean_state, 1, "new_announced", "email")
        assert is_notified(clean_state, 1, "new_announced", "email")
        assert not is_notified(clean_state, 1, "new_announced", "telegram")

    def test_any_channel(self, clean_state):
        """is_notified_any_channel checks across all channels."""
        c = _make_company(1)
        update_company(clean_state, c)
        assert not is_notified_any_channel(clean_state, 1, "new_announced")
        mark_notified(clean_state, 1, "new_announced", "telegram")
        assert is_notified_any_channel(clean_state, 1, "new_announced")

    def test_idempotent_mark(self, clean_state):
        """Marking the same notification twice doesn't duplicate."""
        c = _make_company(1)
        update_company(clean_state, c)
        mark_notified(clean_state, 1, "new_announced", "email")
        mark_notified(clean_state, 1, "new_announced", "email")
        notifs = get_notifs(clean_state, 1)
        assert list(notifs["new_announced"]).count("email") == 1

    def test_per_channel_independence(self, clean_state):
        """Channels are tracked independently."""
        c = _make_company(1)
        update_company(clean_state, c)
        mark_notified(clean_state, 1, "new_announced", "email")
        assert is_notified(clean_state, 1, "new_announced", "email")
        assert not is_notified(clean_state, 1, "new_announced", "telegram")

    def test_reset_on_change(self, clean_state):
        """Resetting a notif key clears it for re-notification."""
        c = _make_company(1)
        update_company(clean_state, c)
        mark_notified(clean_state, 1, "reminder_24h", "email")
        assert is_notified(clean_state, 1, "reminder_24h", "email")
        reset_notif_on_change(clean_state, 1, "reminder_24h")
        assert not is_notified(clean_state, 1, "reminder_24h", "email")


class TestCompanyManagement:
    """Tests for company CRUD in state."""

    def test_update_preserves_notifs(self, clean_state):
        """Updating a company preserves existing notification tracking."""
        c = _make_company(1)
        update_company(clean_state, c)
        mark_notified(clean_state, 1, "new_announced", "email")

        # Update the company (e.g., deadline changed)
        c2 = _make_company(1, deadline=datetime(2026, 11, 1, 12, 0, tzinfo=IST))
        update_company(clean_state, c2)

        assert is_notified(clean_state, 1, "new_announced", "email")

    def test_remove_company(self, clean_state):
        """Removing a company deletes it from state."""
        c = _make_company(1)
        update_company(clean_state, c)
        assert "1" in clean_state["companies"]
        remove_company(clean_state, 1)
        assert "1" not in clean_state["companies"]

    def test_get_companies(self, clean_state):
        """get_companies returns Company objects keyed by ID."""
        c1 = _make_company(1)
        c2 = _make_company(2)
        update_company(clean_state, c1)
        update_company(clean_state, c2)
        companies = get_companies(clean_state)
        assert 1 in companies
        assert 2 in companies
        assert isinstance(companies[1], Company)


class TestPruning:
    """Tests for expired company pruning."""

    def test_prune_old_companies(self, clean_state):
        """Companies past deadline + 30 days are pruned."""
        old_deadline = datetime(2026, 9, 1, 12, 0, tzinfo=IST)  # 51 days ago from now
        c = _make_company(1, deadline=old_deadline)
        update_company(clean_state, c)

        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)
        pruned = prune_expired(clean_state, now)
        assert pruned == 1
        assert "1" not in clean_state["companies"]

    def test_keep_recent_companies(self, clean_state):
        """Companies within 30 days of deadline are kept."""
        recent_deadline = datetime(2026, 10, 15, 12, 0, tzinfo=IST)  # 7 days ago
        c = _make_company(1, deadline=recent_deadline)
        update_company(clean_state, c)

        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)
        pruned = prune_expired(clean_state, now)
        assert pruned == 0
        assert "1" in clean_state["companies"]

    def test_keep_no_deadline(self, clean_state):
        """Companies with no deadline are never pruned."""
        c = _make_company(1, deadline=None)
        update_company(clean_state, c)

        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)
        pruned = prune_expired(clean_state, now)
        assert pruned == 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_company(
    cid: int,
    name: str = "Test Co",
    deadline: datetime = None,
    form_start: datetime = None,
) -> Company:
    """Create a test Company with minimal fields."""
    return Company(
        id=cid,
        company=name,
        company_code=f"TEST{cid:03d}",
        placementtype="Full Time",
        maxPackage="10.0",
        minPackage="5.0",
        tpoprogram="VIT-BTech-Computer Engineering",
        academicyear="2026-27",
        isactive=True,
        form_start=form_start,
        deadline=deadline,
    )
