"""
conftest.py – Shared pytest fixtures for the test suite.

Provides:
- Sample API response data.
- Fake clock for time-dependent tests.
- Clean state fixtures.
- Mocked notification channels.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

# Ensure src is on the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def sample_response():
    """Load the sample API response."""
    sample_path = Path(__file__).parent.parent / "samples" / "response.json"
    with open(sample_path) as f:
        return json.load(f)


@pytest.fixture
def sample_company_list(sample_response):
    """Just the company_list from the sample response."""
    return sample_response["company_list"]


@pytest.fixture
def clean_state():
    """A fresh default state."""
    from notifier.state import default_state
    return default_state()


@pytest.fixture
def populated_state(clean_state, sample_response):
    """State populated with sample companies."""
    from notifier.parser import parse_response, filter_by_program
    from notifier.state import update_company

    companies = parse_response(sample_response)
    for c in companies:
        update_company(clean_state, c)

    clean_state["first_run_done"] = True
    clean_state["last_poll_at"] = datetime(2026, 10, 22, 12, 0, tzinfo=IST).isoformat()
    return clean_state


@pytest.fixture
def now():
    """A fixed 'now' for deterministic tests: 2026-10-22 15:00 IST."""
    return datetime(2026, 10, 22, 15, 0, tzinfo=IST)


@pytest.fixture
def mock_email():
    """Mock all email sending functions."""
    with patch("notifier.channels.email._send_email", return_value=True) as m:
        yield m


@pytest.fixture
def mock_telegram():
    """Mock Telegram sendMessage."""
    with patch("notifier.channels.telegram._send_message", return_value=True) as m:
        yield m


@pytest.fixture
def mock_telegram_updates():
    """Mock Telegram getUpdates."""
    with patch("notifier.channels.telegram._get_updates", return_value=[]) as m:
        yield m


@pytest.fixture
def tmp_state_file(tmp_path):
    """Provide a temporary state file path."""
    return str(tmp_path / "state.json")


@pytest.fixture(autouse=True)
def set_test_env(monkeypatch):
    """Set minimal env vars for tests. Clears secrets."""
    monkeypatch.setenv("PORTAL_USERNAME", "test_user")
    monkeypatch.setenv("PORTAL_PASSWORD", "test_pass")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("GMAIL_ADDRESS", "test@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "test_app_password")
    monkeypatch.setenv("NOTIFY_EMAIL_TO", "user@example.com")
    # Reload config to pick up test values
    import notifier.config as cfg
    monkeypatch.setattr(cfg, "PORTAL_USERNAME", "test_user")
    monkeypatch.setattr(cfg, "PORTAL_PASSWORD", "test_pass")
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", "test_token")
    monkeypatch.setattr(cfg, "TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr(cfg, "GMAIL_ADDRESS", "test@gmail.com")
    monkeypatch.setattr(cfg, "GMAIL_APP_PASSWORD", "test_app_password")
    monkeypatch.setattr(cfg, "NOTIFY_EMAIL_TO", "user@example.com")
