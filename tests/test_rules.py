"""
test_rules.py – Tests for business rules.

Covers:
- Company diffing (new, start_changed, deadline_changed).
- Reminder rules (24h, 2h, registration_open).
- Most-urgent-only reminder logic.
- Never remind after deadline.
- Poll scheduling (due, not due, grace window, no double poll, quiet hours).
- Pause/resume/heartbeat logic.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from notifier.rules import (
    diff_companies, get_due_reminders, is_poll_due,
    is_paused, is_heartbeat_due, next_poll_time,
)
from notifier.models import Company
import notifier.config as config

IST = ZoneInfo("Asia/Kolkata")


class TestDiffCompanies:
    """Tests for the diff engine."""

    def test_new_company(self):
        """Company in new but not old is detected as new."""
        old = {}
        new = {1: _make_company(1)}
        changes = diff_companies(old, new)
        assert len(changes) == 1
        assert changes[0].change_type == "new"
        assert changes[0].company.id == 1

    def test_no_change(self):
        """Identical snapshots produce no changes."""
        c = _make_company(1)
        old = {1: c}
        new = {1: c}
        changes = diff_companies(old, new)
        assert len(changes) == 0

    def test_start_changed(self):
        """Changed form_start is detected."""
        old = {1: _make_company(1, form_start=datetime(2026, 10, 25, 9, 0, tzinfo=IST))}
        new = {1: _make_company(1, form_start=datetime(2026, 10, 26, 10, 0, tzinfo=IST))}
        changes = diff_companies(old, new)
        assert len(changes) == 1
        assert changes[0].change_type == "start_changed"

    def test_deadline_changed(self):
        """Changed deadline is detected."""
        old = {1: _make_company(1, deadline=datetime(2026, 10, 30, 17, 0, tzinfo=IST))}
        new = {1: _make_company(1, deadline=datetime(2026, 11, 1, 12, 0, tzinfo=IST))}
        changes = diff_companies(old, new)
        assert len(changes) == 1
        assert changes[0].change_type == "deadline_changed"

    def test_none_to_value(self):
        """None → value is detected as a change."""
        old = {1: _make_company(1, form_start=None)}
        new = {1: _make_company(1, form_start=datetime(2026, 10, 25, 9, 0, tzinfo=IST))}
        changes = diff_companies(old, new)
        assert len(changes) == 1
        assert changes[0].change_type == "start_changed"
        assert changes[0].old_value == "Not set"

    def test_both_start_and_deadline_changed(self):
        """Both form_start and deadline changes produce two change records."""
        old = {1: _make_company(1,
                                form_start=datetime(2026, 10, 25, 9, 0, tzinfo=IST),
                                deadline=datetime(2026, 10, 30, 17, 0, tzinfo=IST))}
        new = {1: _make_company(1,
                                form_start=datetime(2026, 10, 26, 10, 0, tzinfo=IST),
                                deadline=datetime(2026, 11, 1, 12, 0, tzinfo=IST))}
        changes = diff_companies(old, new)
        assert len(changes) == 2
        types = {c.change_type for c in changes}
        assert "start_changed" in types
        assert "deadline_changed" in types

    def test_removed_company_not_detected(self):
        """Companies removed from the portal are NOT detected by diff."""
        old = {1: _make_company(1)}
        new = {}
        changes = diff_companies(old, new)
        assert len(changes) == 0


class TestDueReminders:
    """Tests for reminder scheduling."""

    def test_registration_open(self):
        """Reminder fires when now >= form_start."""
        c = _make_company(1,
                          form_start=datetime(2026, 10, 22, 10, 0, tzinfo=IST),
                          deadline=datetime(2026, 10, 30, 17, 0, tzinfo=IST))
        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)
        due = get_due_reminders(c, now, {})
        assert "registration_open" in due

    def test_registration_not_open_yet(self):
        """No reminder when now < form_start."""
        c = _make_company(1,
                          form_start=datetime(2026, 10, 25, 10, 0, tzinfo=IST),
                          deadline=datetime(2026, 10, 30, 17, 0, tzinfo=IST))
        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)
        due = get_due_reminders(c, now, {})
        assert "registration_open" not in due

    def test_24h_reminder(self):
        """24h reminder fires when within 24 hours of deadline."""
        deadline = datetime(2026, 10, 23, 15, 0, tzinfo=IST)
        c = _make_company(1, deadline=deadline)
        now = datetime(2026, 10, 22, 16, 0, tzinfo=IST)  # 23 hours before
        due = get_due_reminders(c, now, {})
        assert "reminder_24h" in due

    def test_2h_reminder(self):
        """2h reminder fires when within 2 hours of deadline."""
        deadline = datetime(2026, 10, 22, 17, 0, tzinfo=IST)
        c = _make_company(1, deadline=deadline)
        now = datetime(2026, 10, 22, 15, 30, tzinfo=IST)  # 1.5 hours before
        due = get_due_reminders(c, now, {})
        assert "reminder_2h" in due

    def test_most_urgent_only(self):
        """When first seen inside both windows, only the most urgent fires."""
        deadline = datetime(2026, 10, 22, 16, 0, tzinfo=IST)
        c = _make_company(1, deadline=deadline)
        now = datetime(2026, 10, 22, 14, 30, tzinfo=IST)  # within both 24h and 2h
        # No previous notifications sent
        due = get_due_reminders(c, now, {})
        # Should get only 2h (most urgent), not 24h
        assert "reminder_2h" in due
        assert "reminder_24h" not in due

    def test_never_after_deadline(self):
        """No reminders after the deadline has passed."""
        deadline = datetime(2026, 10, 22, 12, 0, tzinfo=IST)
        c = _make_company(1, deadline=deadline)
        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)  # 3 hours past
        due = get_due_reminders(c, now, {})
        assert "reminder_24h" not in due
        assert "reminder_2h" not in due

    def test_already_notified(self):
        """Don't re-send already-sent reminders."""
        deadline = datetime(2026, 10, 23, 15, 0, tzinfo=IST)
        c = _make_company(1, deadline=deadline)
        now = datetime(2026, 10, 22, 16, 0, tzinfo=IST)
        notified = {"reminder_24h": {"email", "telegram"}}
        due = get_due_reminders(c, now, notified)
        assert "reminder_24h" not in due

    def test_no_deadline(self):
        """No reminders when deadline is None."""
        c = _make_company(1, deadline=None)
        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)
        due = get_due_reminders(c, now, {})
        assert "reminder_24h" not in due
        assert "reminder_2h" not in due

    def test_no_form_start(self):
        """No registration_open when form_start is None."""
        c = _make_company(1, form_start=None)
        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)
        due = get_due_reminders(c, now, {})
        assert "registration_open" not in due


class TestPollScheduling:
    """Tests for poll scheduling logic."""

    def test_poll_due(self, monkeypatch):
        """Poll is due when a scheduled time has passed since last poll."""
        monkeypatch.setattr(config, "POLL_TIMES_IST", ["10:00", "14:00", "18:00"])
        now = datetime(2026, 10, 22, 14, 10, tzinfo=IST)
        last_poll = datetime(2026, 10, 22, 10, 5, tzinfo=IST)
        assert is_poll_due(now, last_poll, paused=False) is True

    def test_poll_not_due(self, monkeypatch):
        """Poll is not due when already polled for the current slot."""
        monkeypatch.setattr(config, "POLL_TIMES_IST", ["10:00", "14:00", "18:00"])
        now = datetime(2026, 10, 22, 14, 10, tzinfo=IST)
        last_poll = datetime(2026, 10, 22, 14, 5, tzinfo=IST)
        assert is_poll_due(now, last_poll, paused=False) is False

    def test_poll_grace_window(self, monkeypatch):
        """Poll works within the grace window after a scheduled time."""
        monkeypatch.setattr(config, "POLL_TIMES_IST", ["14:00"])
        monkeypatch.setattr(config, "POLL_GRACE_WINDOW_SECONDS", 2100)
        now = datetime(2026, 10, 22, 14, 30, tzinfo=IST)  # 30 min after slot
        assert is_poll_due(now, None, paused=False) is True

    def test_poll_outside_grace_window(self, monkeypatch):
        """Poll doesn't trigger if too far past the slot."""
        monkeypatch.setattr(config, "POLL_TIMES_IST", ["10:00"])
        monkeypatch.setattr(config, "POLL_GRACE_WINDOW_SECONDS", 2100)
        now = datetime(2026, 10, 22, 11, 0, tzinfo=IST)  # 60 min after, > 35 min grace
        assert is_poll_due(now, None, paused=False) is False

    def test_no_double_poll(self, monkeypatch):
        """Polling at 14:05 for the 14:00 slot, then at 14:20 should not re-poll."""
        monkeypatch.setattr(config, "POLL_TIMES_IST", ["14:00"])
        last_poll = datetime(2026, 10, 22, 14, 5, tzinfo=IST)
        now = datetime(2026, 10, 22, 14, 20, tzinfo=IST)
        assert is_poll_due(now, last_poll, paused=False) is False

    def test_quiet_hours(self, monkeypatch):
        """No polls during quiet hours (no slots scheduled)."""
        monkeypatch.setattr(config, "POLL_TIMES_IST", ["08:00", "10:00", "22:00"])
        monkeypatch.setattr(config, "POLL_GRACE_WINDOW_SECONDS", 2100)
        now = datetime(2026, 10, 23, 3, 0, tzinfo=IST)  # 3 AM
        assert is_poll_due(now, None, paused=False) is False

    def test_paused_skips_poll(self, monkeypatch):
        """When paused, poll is never due."""
        monkeypatch.setattr(config, "POLL_TIMES_IST", ["14:00"])
        now = datetime(2026, 10, 22, 14, 10, tzinfo=IST)
        assert is_poll_due(now, None, paused=True) is False

    def test_first_poll_ever(self, monkeypatch):
        """First poll (last_poll_at=None) triggers correctly."""
        monkeypatch.setattr(config, "POLL_TIMES_IST", ["10:00", "14:00"])
        now = datetime(2026, 10, 22, 10, 15, tzinfo=IST)
        assert is_poll_due(now, None, paused=False) is True


class TestPauseResume:
    """Tests for pause/resume logic."""

    def test_is_paused(self):
        """System is paused when pause_until is in the future."""
        state = {
            "pause_until": (datetime.now(IST) + timedelta(hours=1)).isoformat()
        }
        assert is_paused(state) is True

    def test_is_not_paused(self):
        """System is not paused when pause_until is in the past."""
        state = {
            "pause_until": (datetime.now(IST) - timedelta(hours=1)).isoformat()
        }
        assert is_paused(state) is False

    def test_no_pause_until(self):
        """System is not paused when pause_until is None."""
        state = {"pause_until": None}
        assert is_paused(state) is False


class TestHeartbeat:
    """Tests for heartbeat logic."""

    def test_heartbeat_due_never_sent(self):
        """Heartbeat is due when never sent before."""
        state = {"last_heartbeat_at": None}
        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)
        assert is_heartbeat_due(state, now) is True

    def test_heartbeat_due_after_interval(self, monkeypatch):
        """Heartbeat is due after the configured interval."""
        monkeypatch.setattr(config, "HEARTBEAT_INTERVAL_HOURS", 168)
        state = {
            "last_heartbeat_at": (datetime(2026, 10, 14, 15, 0, tzinfo=IST)).isoformat()
        }
        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)  # 8 days later
        assert is_heartbeat_due(state, now) is True

    def test_heartbeat_not_due(self, monkeypatch):
        """Heartbeat is not due within the interval."""
        monkeypatch.setattr(config, "HEARTBEAT_INTERVAL_HOURS", 168)
        state = {
            "last_heartbeat_at": (datetime(2026, 10, 20, 15, 0, tzinfo=IST)).isoformat()
        }
        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)  # only 2 days
        assert is_heartbeat_due(state, now) is False


class TestNextPollTime:
    """Tests for next_poll_time display helper."""

    def test_next_poll_today(self, monkeypatch):
        """Next poll is a later slot today."""
        monkeypatch.setattr(config, "POLL_TIMES_IST", ["10:00", "14:00", "18:00"])
        now = datetime(2026, 10, 22, 12, 0, tzinfo=IST)
        npt = next_poll_time(now)
        assert npt.hour == 14

    def test_next_poll_tomorrow(self, monkeypatch):
        """Next poll wraps to tomorrow's first slot."""
        monkeypatch.setattr(config, "POLL_TIMES_IST", ["10:00", "14:00"])
        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)
        npt = next_poll_time(now)
        assert npt.day == 23
        assert npt.hour == 10


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_company(
    cid: int = 1,
    form_start=None,
    deadline=None,
) -> Company:
    return Company(
        id=cid,
        company="Test Co",
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
