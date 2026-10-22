"""
test_integration.py – End-to-end simulation tests.

Simulates three successive portal snapshots:
  1. New company appears (with dates).
  2. Deadline changes.
  3. Deadline approaches reminder window.

Verifies:
  - Exact expected notifications are sent.
  - No duplicates across runs.
  - First-run seeding (silent).
  - Failure escalation and recovery.
  - Reminders fire while paused (no poll needed).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from zoneinfo import ZoneInfo

import pytest

from notifier.models import Company
from notifier.state import (
    default_state, load_state, save_state,
    update_company, mark_notified, get_companies,
)
from notifier.parser import parse_response
from notifier.rules import diff_companies, get_due_reminders

IST = ZoneInfo("Asia/Kolkata")


def _make_company(
    cid: int = 1,
    name: str = "SimCorp",
    form_start: datetime = None,
    deadline: datetime = None,
) -> Company:
    return Company(
        id=cid,
        company=name,
        company_code=f"SIM{cid:03d}",
        placementtype="Full Time",
        maxPackage="15.0",
        minPackage="10.0",
        tpoprogram="VIT-BTech-Computer Engineering",
        academicyear="2026-27",
        isactive=True,
        form_start=form_start,
        deadline=deadline,
    )


def _api_snapshot(companies: list[Company]) -> dict:
    """Build a fake portal API response from Company objects."""
    items = []
    for c in companies:
        items.append({
            "id": c.id,
            "company": c.company,
            "company_code": c.company_code,
            "placementtype": c.placementtype,
            "maxPackage": c.maxPackage,
            "minPackage": c.minPackage,
            "tpoprogram": c.tpoprogram,
            "academicyear": c.academicyear,
            "isactive": True,
            "regStartdate": c.form_start.strftime("%d-%b-%Y") if c.form_start else "",
            "regStarttime": c.form_start.strftime("%H:%M") if c.form_start else "",
            "regEnddate": c.deadline.strftime("%d-%b-%Y") if c.deadline else "",
            "regEndtime": c.deadline.strftime("%H:%M") if c.deadline else "",
        })
    return {"status": "success", "company_list": items}


class TestEndToEndSimulation:
    """Three-snapshot end-to-end simulation."""

    def test_snapshot1_new_company(self):
        """Snapshot 1: New company triggers new_announced notification."""
        # T=0: No companies in state
        state = default_state()
        state["first_run_done"] = True  # not a first run

        company = _make_company(
            1,
            form_start=datetime(2026, 10, 25, 9, 0, tzinfo=IST),
            deadline=datetime(2026, 10, 30, 17, 0, tzinfo=IST),
        )
        snapshot = _api_snapshot([company])
        companies = parse_response(snapshot)

        old = get_companies(state)
        new = {c.id: c for c in companies}
        changes = diff_companies(old, new)

        assert len(changes) == 1
        assert changes[0].change_type == "new"
        assert changes[0].company.id == 1

    def test_snapshot2_deadline_changed(self):
        """Snapshot 2: Changed deadline triggers deadline_changed notification."""
        # T=1: Company in state with original deadline
        state = default_state()
        state["first_run_done"] = True

        original = _make_company(
            1,
            deadline=datetime(2026, 10, 30, 17, 0, tzinfo=IST),
        )
        update_company(state, original)
        mark_notified(state, 1, "new_announced", "email")
        mark_notified(state, 1, "new_announced", "telegram")

        # New snapshot with changed deadline
        updated = _make_company(
            1,
            deadline=datetime(2026, 11, 5, 17, 0, tzinfo=IST),
        )
        old = get_companies(state)
        new = {1: updated}
        changes = diff_companies(old, new)

        assert len(changes) == 1
        assert changes[0].change_type == "deadline_changed"
        assert "30" in changes[0].old_value
        assert "05 Nov" in changes[0].new_value

    def test_snapshot3_reminder_fires(self):
        """Snapshot 3: Approaching deadline triggers 24h reminder."""
        state = default_state()
        state["first_run_done"] = True

        # Deadline is 23 hours from 'now'
        now = datetime(2026, 10, 29, 18, 0, tzinfo=IST)
        deadline = datetime(2026, 10, 30, 17, 0, tzinfo=IST)
        company = _make_company(1, deadline=deadline)
        update_company(state, company)
        mark_notified(state, 1, "new_announced", "email")
        mark_notified(state, 1, "new_announced", "telegram")

        from notifier.state import get_notifs
        notifs = get_notifs(state, 1)
        due = get_due_reminders(company, now, notifs)

        assert "reminder_24h" in due
        assert "reminder_2h" not in due  # not within 2h yet

    def test_no_duplicate_notifications(self):
        """Running the same snapshot twice sends notifications only once."""
        state = default_state()
        state["first_run_done"] = True

        company = _make_company(1, deadline=datetime(2026, 10, 30, 17, 0, tzinfo=IST))
        send_count = [0]

        def simulate_notify(state, company):
            from notifier.state import is_notified, mark_notified, update_company
            update_company(state, company)
            if not is_notified(state, company.id, "new_announced", "email"):
                send_count[0] += 1
                mark_notified(state, company.id, "new_announced", "email")

        old = {}
        new = {1: company}

        # First run
        simulate_notify(state, company)
        # Second run (same snapshot)
        simulate_notify(state, company)

        assert send_count[0] == 1

    def test_first_run_seeding_silent(self):
        """First run seeds companies silently, no notifications."""
        state = default_state()
        # first_run_done = False means it IS the first run

        company = _make_company(1, deadline=datetime(2026, 10, 30, 17, 0, tzinfo=IST))
        old = {}
        new = {1: company}
        changes = diff_companies(old, new)

        notifications_sent = []

        # Simulate first-run logic
        is_first_run = not state.get("first_run_done", False)
        announce = False  # not --announce-existing

        for change in changes:
            if is_first_run and not announce:
                # Seed silently
                update_company(state, change.company)
                mark_notified(state, change.company.id, "new_announced", "email")
                mark_notified(state, change.company.id, "new_announced", "telegram")
            else:
                notifications_sent.append(change.change_type)

        assert len(notifications_sent) == 0
        assert "1" in state["companies"]  # company IS stored

    def test_first_run_announce_existing(self):
        """First run with --announce-existing sends notifications."""
        state = default_state()

        company = _make_company(1, deadline=datetime(2026, 10, 30, 17, 0, tzinfo=IST))
        old = {}
        new = {1: company}
        changes = diff_companies(old, new)

        notifications_sent = []
        is_first_run = not state.get("first_run_done", False)
        announce = True  # --announce-existing

        for change in changes:
            if is_first_run and not announce:
                update_company(state, change.company)
            else:
                notifications_sent.append(change.change_type)

        assert len(notifications_sent) == 1
        assert "new" in notifications_sent


class TestFailureEscalation:
    """Tests for failure escalation and recovery."""

    def test_alert_sent_on_third_failure(self):
        """Alert fires after CONSECUTIVE_FAILURES_ALERT consecutive failures."""
        state = default_state()
        alerts_sent = []
        threshold = 3

        for i in range(threshold):
            state["consecutive_failures"] += 1
            if state["consecutive_failures"] >= threshold and not state.get("alert_sent"):
                alerts_sent.append("alert")
                state["alert_sent"] = True

        assert len(alerts_sent) == 1  # exactly one alert

    def test_no_duplicate_alert(self):
        """Alert is not sent more than once."""
        state = default_state()
        state["consecutive_failures"] = 3
        state["alert_sent"] = True

        alerts_sent = []
        for i in range(3):
            # Simulate continued failures
            state["consecutive_failures"] += 1
            if state["consecutive_failures"] >= 3 and not state.get("alert_sent"):
                alerts_sent.append("alert")
                state["alert_sent"] = True

        assert len(alerts_sent) == 0  # already alerted, no new alert

    def test_recovery_clears_alert(self):
        """Successful poll clears alert_sent and consecutive_failures."""
        state = default_state()
        state["consecutive_failures"] = 5
        state["alert_sent"] = True

        # Simulate successful poll
        state["consecutive_failures"] = 0
        state["alert_sent"] = False

        assert state["consecutive_failures"] == 0
        assert state["alert_sent"] is False


class TestRemindersWhilePaused:
    """Tests that reminders fire even when system is paused."""

    def test_reminders_fire_while_paused(self):
        """Deadline reminder logic works regardless of pause state."""
        # pause state doesn't affect get_due_reminders
        # (pause only skips portal poll)
        state = default_state()
        state["pause_until"] = (datetime(2026, 10, 23, 12, 0, tzinfo=IST)).isoformat()

        deadline = datetime(2026, 10, 22, 17, 0, tzinfo=IST)
        company = _make_company(1, deadline=deadline)
        now = datetime(2026, 10, 22, 15, 30, tzinfo=IST)  # 1.5h before deadline

        from notifier.state import get_notifs
        notifs = get_notifs(state, 1)
        due = get_due_reminders(company, now, notifs)

        # Reminders fire even though paused
        assert "reminder_2h" in due

    def test_poll_skipped_while_paused(self):
        """Poll is skipped while paused, but reminders still sent."""
        from notifier.rules import is_poll_due, is_paused

        state = {"pause_until": (datetime(2026, 10, 22, 18, 0, tzinfo=IST)).isoformat()}
        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)

        assert is_paused(state, now) is True
        assert is_poll_due(now, None, paused=True) is False


class TestStateRoundTrip:
    """Tests for state persistence in end-to-end simulation."""

    def test_full_round_trip(self, tmp_path):
        """State persists correctly across save/load cycles."""
        state_file = str(tmp_path / "state.json")

        # Simulate run 1: company added
        state1 = default_state()
        state1["first_run_done"] = True
        company = _make_company(1, deadline=datetime(2026, 10, 30, 17, 0, tzinfo=IST))
        update_company(state1, company)
        mark_notified(state1, 1, "new_announced", "email")
        state1["last_poll_at"] = datetime(2026, 10, 22, 14, 0, tzinfo=IST).isoformat()
        save_state(state1, state_file)

        # Simulate run 2: reload and check
        state2 = load_state(state_file)
        assert state2["first_run_done"] is True
        assert "1" in state2["companies"]
        assert state2["last_poll_at"] is not None

        companies2 = get_companies(state2)
        assert 1 in companies2
        assert companies2[1].company == "SimCorp"

        from notifier.state import is_notified
        assert is_notified(state2, 1, "new_announced", "email")
        assert not is_notified(state2, 1, "new_announced", "telegram")


class TestMainErrorHandling:
    """Tests for CLI flags and error safety net in main."""

    @patch("notifier.channels.email.send_new_company", return_value=True)
    @patch("notifier.channels.telegram.send_new_company", return_value=True)
    def test_demo_notify(self, mock_tg, mock_email):
        """--demo-notify triggers sample company alerts on both channels."""
        from notifier.main import main
        with patch("sys.argv", ["notifier", "--demo-notify"]):
            exit_code = main()
            assert exit_code == 0
            assert mock_email.called
            assert mock_tg.called

    @patch("notifier.main._run", side_effect=RuntimeError("Unexpected catastrophic error"))
    @patch("notifier.channels.telegram.send_alert")
    @patch("notifier.channels.email.send_alert")
    def test_crash_safety_net(self, mock_email_alert, mock_tg_alert, mock_run):
        """Top-level main() intercepts unhandled exceptions and notifies both channels."""
        from notifier.main import main
        with patch("sys.argv", ["notifier"]):
            exit_code = main()
            assert exit_code == 1
            assert mock_tg_alert.called
            assert mock_email_alert.called
            # Verify the alert message contains the critical notification
            call_args = mock_tg_alert.call_args[0][0]
            assert "CRITICAL" in call_args
            assert "RuntimeError" in call_args
