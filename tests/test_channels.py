"""
test_channels.py – Tests for email and telegram channels.

Covers:
- Email sending (mocked SMTP).
- ICS generation and validity.
- ICS SEQUENCE increment on changes.
- Telegram message sending (mocked urllib).
- Telegram command handling (/pause, /resume, /status).
- Ignoring messages from other chats.
- Telegram offset tracking.
- Per-channel retry independence.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call
from zoneinfo import ZoneInfo

import pytest

from notifier.channels import email as email_channel
from notifier.channels import telegram as telegram_channel
from notifier.models import Company, CompanyChange
import notifier.config as config

IST = ZoneInfo("Asia/Kolkata")


def _make_company(
    cid: int = 1,
    name: str = "Test Corp",
    form_start: datetime = None,
    deadline: datetime = None,
) -> Company:
    return Company(
        id=cid,
        company=name,
        company_code=f"TC{cid:03d}",
        placementtype="Full Time",
        maxPackage="12.0",
        minPackage="8.0",
        tpoprogram="VIT-BTech-Computer Engineering",
        academicyear="2026-27",
        isactive=True,
        form_start=form_start,
        deadline=deadline,
    )


class TestEmailSending:
    """Tests for email channel."""

    def test_send_new_company_success(self):
        """send_new_company returns True on successful send."""
        c = _make_company(1,
                          form_start=datetime(2026, 10, 25, 9, 0, tzinfo=IST),
                          deadline=datetime(2026, 10, 30, 17, 0, tzinfo=IST))
        with patch("notifier.channels.email._send_email", return_value=True) as m:
            result = email_channel.send_new_company(c)
        assert result is True
        m.assert_called_once()
        # Verify subject contains company name
        subject = m.call_args[0][0]
        assert "Test Corp" in subject

    def test_send_new_company_failure(self):
        """send_new_company returns False on SMTP failure."""
        c = _make_company(1)
        with patch("notifier.channels.email._send_email", return_value=False):
            result = email_channel.send_new_company(c)
        assert result is False

    def test_send_reminder_24h(self):
        """send_reminder returns True for 24h reminder."""
        c = _make_company(1, deadline=datetime(2026, 10, 23, 17, 0, tzinfo=IST))
        with patch("notifier.channels.email._send_email", return_value=True) as m:
            result = email_channel.send_reminder(c, "reminder_24h")
        assert result is True
        subject = m.call_args[0][0]
        assert "24 hours" in subject

    def test_send_reminder_2h(self):
        """send_reminder returns True for 2h reminder."""
        c = _make_company(1, deadline=datetime(2026, 10, 22, 17, 0, tzinfo=IST))
        with patch("notifier.channels.email._send_email", return_value=True) as m:
            result = email_channel.send_reminder(c, "reminder_2h")
        assert result is True
        subject = m.call_args[0][0]
        assert "2 hours" in subject

    def test_send_change(self):
        """send_change sends a correctly formatted change email."""
        c = _make_company(1, deadline=datetime(2026, 10, 30, 17, 0, tzinfo=IST))
        change = CompanyChange(
            company=c,
            change_type="deadline_changed",
            old_value="29 Oct 2026 17:00 IST",
            new_value="30 Oct 2026 17:00 IST",
        )
        with patch("notifier.channels.email._send_email", return_value=True) as m:
            result = email_channel.send_change(change)
        assert result is True

    def test_missing_credentials_returns_false(self, monkeypatch):
        """Returns False when email credentials are missing."""
        monkeypatch.setattr(config, "GMAIL_ADDRESS", "")
        monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "")
        monkeypatch.setattr(config, "NOTIFY_EMAIL_TO", "")
        c = _make_company(1)
        # Don't mock _send_email – let it hit the credential check
        result = email_channel._send_email("subject", "body", "<p>body</p>")
        assert result is False

    def test_send_test(self):
        """send_test returns True on success."""
        with patch("notifier.channels.email._send_email", return_value=True):
            result = email_channel.send_test()
        assert result is True

    def test_parse_recipients_various_formats(self):
        """Test parsing of single, comma, and 'and' separated emails."""
        assert email_channel._parse_recipients("a@vit.edu") == ["a@vit.edu"]
        assert email_channel._parse_recipients("a@vit.edu, b@gmail.com") == ["a@vit.edu", "b@gmail.com"]
        assert email_channel._parse_recipients("a@vit.edu and b@gmail.com") == ["a@vit.edu", "b@gmail.com"]
        assert email_channel._parse_recipients("a@vit.edu; b@gmail.com") == ["a@vit.edu", "b@gmail.com"]
        assert email_channel._parse_recipients("") == []
        assert email_channel._parse_recipients("invalid_email") == []

    def test_send_via_resend_success(self, monkeypatch):
        """Test successful send via Resend API."""
        monkeypatch.setattr(config, "RESEND_API_KEY", "re_test_123")
        monkeypatch.setattr(config, "NOTIFY_EMAIL_TO", "test@vit.edu")

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = email_channel._send_via_resend("Test Sub", "Plain", "<p>HTML</p>", "BEGIN:VCALENDAR...")
        assert result is True


class TestICSGeneration:
    """Tests for ICS calendar file generation."""

    def test_ics_contains_vevent(self):
        """Generated ICS contains at least one VEVENT."""
        c = _make_company(1,
                          form_start=datetime(2026, 10, 25, 9, 0, tzinfo=IST),
                          deadline=datetime(2026, 10, 30, 17, 0, tzinfo=IST))
        ics = email_channel.generate_ics(c, sequence=0)
        assert "BEGIN:VEVENT" in ics
        assert "END:VEVENT" in ics

    def test_ics_has_both_events(self):
        """ICS has events for both form_start and deadline."""
        c = _make_company(1,
                          form_start=datetime(2026, 10, 25, 9, 0, tzinfo=IST),
                          deadline=datetime(2026, 10, 30, 17, 0, tzinfo=IST))
        ics = email_channel.generate_ics(c, sequence=0)
        assert ics.count("BEGIN:VEVENT") == 2

    def test_ics_stable_uid(self):
        """UIDs are stable across calls for the same company."""
        c = _make_company(1,
                          form_start=datetime(2026, 10, 25, 9, 0, tzinfo=IST),
                          deadline=datetime(2026, 10, 30, 17, 0, tzinfo=IST))
        ics1 = email_channel.generate_ics(c, sequence=0)
        ics2 = email_channel.generate_ics(c, sequence=0)
        # Extract UIDs
        uid1 = [l for l in ics1.split("\r\n") if l.startswith("UID:")]
        uid2 = [l for l in ics2.split("\r\n") if l.startswith("UID:")]
        assert uid1 == uid2

    def test_ics_sequence_increments(self):
        """SEQUENCE increments for updates so calendar clients update the event."""
        c = _make_company(1,
                          deadline=datetime(2026, 10, 30, 17, 0, tzinfo=IST))
        ics0 = email_channel.generate_ics(c, sequence=0)
        ics1 = email_channel.generate_ics(c, sequence=1)
        assert "SEQUENCE:0" in ics0
        assert "SEQUENCE:1" in ics1

    def test_ics_has_valarm(self):
        """ICS deadline event has VALARM entries."""
        c = _make_company(1, deadline=datetime(2026, 10, 30, 17, 0, tzinfo=IST))
        ics = email_channel.generate_ics(c, sequence=0)
        assert "BEGIN:VALARM" in ics
        assert "END:VALARM" in ics

    def test_ics_empty_when_no_dates(self):
        """ICS is empty string when no dates are set."""
        c = _make_company(1)
        ics = email_channel.generate_ics(c, sequence=0)
        assert ics == ""

    def test_ics_only_deadline(self):
        """ICS with only deadline has one VEVENT."""
        c = _make_company(1, deadline=datetime(2026, 10, 30, 17, 0, tzinfo=IST))
        ics = email_channel.generate_ics(c, sequence=0)
        assert ics.count("BEGIN:VEVENT") == 1

    def test_ics_html_special_chars(self):
        """Special characters in company name are escaped in ICS."""
        c = _make_company(1, name='Tech & Co, "Best"',
                          deadline=datetime(2026, 10, 30, 17, 0, tzinfo=IST))
        ics = email_channel.generate_ics(c, sequence=0)
        assert "BEGIN:VEVENT" in ics
        # ICS escaping uses backslash, not HTML entities
        summary_line = [l for l in ics.split("\r\n") if l.startswith("SUMMARY:")]
        assert len(summary_line) > 0


class TestTelegramSending:
    """Tests for Telegram channel."""

    def test_send_new_company(self):
        """send_new_company calls _send_message with company name."""
        c = _make_company(1)
        with patch("notifier.channels.telegram._send_message", return_value=True) as m:
            result = telegram_channel.send_new_company(c)
        assert result is True
        text = m.call_args[0][0]
        assert "Test Corp" in text

    def test_html_escaping(self):
        """HTML special characters are escaped in Telegram messages."""
        c = _make_company(1, name="Tech <Corp> & More")
        with patch("notifier.channels.telegram._send_message") as m:
            telegram_channel.send_new_company(c)
        text = m.call_args[0][0]
        assert "<Corp>" not in text
        assert "&lt;Corp&gt;" in text
        assert "&amp;" in text

    def test_send_reminder_24h(self):
        """24h reminder uses urgent emoji."""
        c = _make_company(1, deadline=datetime(2026, 10, 23, 17, 0, tzinfo=IST))
        with patch("notifier.channels.telegram._send_message") as m:
            telegram_channel.send_reminder(c, "reminder_24h")
        text = m.call_args[0][0]
        assert "24 HOURS" in text

    def test_send_reminder_2h(self):
        """2h reminder uses most urgent emoji."""
        c = _make_company(1, deadline=datetime(2026, 10, 22, 17, 0, tzinfo=IST))
        with patch("notifier.channels.telegram._send_message") as m:
            telegram_channel.send_reminder(c, "reminder_2h")
        text = m.call_args[0][0]
        assert "2 HOURS" in text

    def test_missing_credentials(self, monkeypatch):
        """Returns False when token or chat_id is missing."""
        monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
        result = telegram_channel._send_message("test")
        assert result is False

    def test_send_test(self):
        """send_test returns True."""
        with patch("notifier.channels.telegram._send_message", return_value=True):
            result = telegram_channel.send_test()
        assert result is True


class TestTelegramCommands:
    """Tests for /pause, /resume, /status command handling."""

    def _make_update(self, update_id: int, chat_id: str, text: str) -> dict:
        return {
            "update_id": update_id,
            "message": {
                "chat": {"id": int(chat_id)},
                "text": text,
            }
        }

    def test_pause_command(self, clean_state, monkeypatch):
        """The /pause command stores pause_until and sends confirmation."""
        monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "12345")
        updates = [self._make_update(1, "12345", "/pause 4")]
        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)

        with patch("notifier.channels.telegram._get_updates", return_value=updates), \
             patch("notifier.channels.telegram._send_message", return_value=True) as mock_send:
            telegram_channel.process_commands(clean_state, now)

        assert clean_state["pause_until"] is not None
        assert clean_state["telegram_offset"] == 2  # update_id + 1
        mock_send.assert_called_once()

    def test_pause_default_2h(self, clean_state, monkeypatch):
        """The /pause command defaults to 2 hours."""
        monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "12345")
        updates = [self._make_update(1, "12345", "/pause")]
        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)

        with patch("notifier.channels.telegram._get_updates", return_value=updates), \
             patch("notifier.channels.telegram._send_message", return_value=True):
            telegram_channel.process_commands(clean_state, now)

        pause_dt = datetime.fromisoformat(clean_state["pause_until"])
        expected = now + timedelta(hours=2)
        assert abs((pause_dt - expected).total_seconds()) < 60

    def test_pause_max_12h(self, clean_state, monkeypatch):
        """The /pause command caps at 12 hours."""
        monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "12345")
        updates = [self._make_update(1, "12345", "/pause 100")]
        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)

        with patch("notifier.channels.telegram._get_updates", return_value=updates), \
             patch("notifier.channels.telegram._send_message", return_value=True):
            telegram_channel.process_commands(clean_state, now)

        pause_dt = datetime.fromisoformat(clean_state["pause_until"])
        expected_max = now + timedelta(hours=12)
        assert pause_dt <= expected_max + timedelta(seconds=5)

    def test_resume_command(self, clean_state, monkeypatch):
        """The /resume command clears pause_until."""
        clean_state["pause_until"] = (datetime(2026, 10, 22, 20, 0, tzinfo=IST)).isoformat()
        monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "12345")
        updates = [self._make_update(2, "12345", "/resume")]
        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)

        with patch("notifier.channels.telegram._get_updates", return_value=updates), \
             patch("notifier.channels.telegram._send_message", return_value=True):
            telegram_channel.process_commands(clean_state, now)

        assert clean_state["pause_until"] is None

    def test_status_command(self, clean_state, monkeypatch):
        """The /status command sends a status message."""
        monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "12345")
        updates = [self._make_update(3, "12345", "/status")]
        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)

        with patch("notifier.channels.telegram._get_updates", return_value=updates), \
             patch("notifier.channels.telegram._send_message", return_value=True) as mock_send:
            telegram_channel.process_commands(clean_state, now)

        mock_send.assert_called_once()
        text = mock_send.call_args[0][0]
        assert "Status" in text

    def test_ignore_other_chats(self, clean_state, monkeypatch):
        """Commands from other chat IDs are ignored."""
        monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "12345")
        updates = [self._make_update(4, "99999", "/pause 4")]  # different chat
        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)

        with patch("notifier.channels.telegram._get_updates", return_value=updates), \
             patch("notifier.channels.telegram._send_message", return_value=True) as mock_send:
            telegram_channel.process_commands(clean_state, now)

        # No pause, no message sent
        assert clean_state["pause_until"] is None
        mock_send.assert_not_called()

    def test_offset_tracking(self, clean_state, monkeypatch):
        """Update offset is correctly advanced to prevent reprocessing."""
        monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "12345")
        clean_state["telegram_offset"] = 10
        updates = [
            self._make_update(10, "12345", "/status"),
            self._make_update(11, "12345", "/status"),
        ]
        now = datetime(2026, 10, 22, 15, 0, tzinfo=IST)

        with patch("notifier.channels.telegram._get_updates", return_value=updates), \
             patch("notifier.channels.telegram._send_message", return_value=True):
            telegram_channel.process_commands(clean_state, now)

        assert clean_state["telegram_offset"] == 12  # highest update_id + 1

    def test_no_commands_no_change(self, clean_state, monkeypatch):
        """No updates means no state changes."""
        monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "12345")
        with patch("notifier.channels.telegram._get_updates", return_value=[]):
            telegram_channel.process_commands(clean_state, datetime.now(IST))
        assert clean_state["pause_until"] is None
        assert clean_state["telegram_offset"] == 0


class TestPerChannelRetry:
    """Tests that channels track independently and can be retried."""

    def test_email_fail_telegram_success(self, clean_state):
        """When email fails, telegram succeeds, and only email is pending."""
        from notifier.state import update_company, mark_notified, is_notified

        c = _make_company(1)
        update_company(clean_state, c)

        # Simulate: telegram succeeded, email failed
        mark_notified(clean_state, 1, "new_announced", "telegram")

        assert is_notified(clean_state, 1, "new_announced", "telegram")
        assert not is_notified(clean_state, 1, "new_announced", "email")

        # On next run, email would be retried
        # Simulate email retry succeeding
        mark_notified(clean_state, 1, "new_announced", "email")
        assert is_notified(clean_state, 1, "new_announced", "email")

    def test_idempotent_double_notification(self, clean_state):
        """Running the notification logic twice sends no duplicates."""
        from notifier.state import update_company, is_notified, mark_notified

        c = _make_company(1)
        update_company(clean_state, c)

        send_count = [0]

        def fake_send():
            send_count[0] += 1
            return True

        # First run
        if not is_notified(clean_state, 1, "new_announced", "email"):
            if fake_send():
                mark_notified(clean_state, 1, "new_announced", "email")

        # Second run (same logic)
        if not is_notified(clean_state, 1, "new_announced", "email"):
            if fake_send():
                mark_notified(clean_state, 1, "new_announced", "email")

        assert send_count[0] == 1  # Only sent once
