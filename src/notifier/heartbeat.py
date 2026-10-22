"""
heartbeat.py – Weekly heartbeat notification.

Sends a status message at the configured interval to confirm the system
is alive and working. Also helps keep the GitHub Actions schedule active
(repos with no activity for 60 days get their schedules disabled).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from . import config
from .channels import email as email_channel
from .channels import telegram as telegram_channel
from .rules import is_heartbeat_due

logger = logging.getLogger(__name__)


def maybe_send_heartbeat(state: dict[str, Any], now: datetime) -> bool:
    """
    Send a heartbeat if one is due.

    Returns True if a heartbeat was sent (or wasn't due), False on failure.
    """
    if not is_heartbeat_due(state, now):
        return True

    company_count = len(state.get("companies", {}))
    last_success = state.get("last_success_at", "never")
    last_poll = state.get("last_poll_at", "never")
    failures = state.get("consecutive_failures", 0)

    message = (
        f"System alive and monitoring.\n"
        f"Active companies: {company_count}\n"
        f"Last successful poll: {last_poll}\n"
        f"Last successful tick: {last_success}\n"
        f"Consecutive failures: {failures}"
    )

    email_ok = email_channel.send_heartbeat(message)
    telegram_ok = telegram_channel.send_heartbeat(message)

    if email_ok or telegram_ok:
        state["last_heartbeat_at"] = now.isoformat()
        logger.info("Heartbeat sent")
        return True

    logger.warning("Failed to send heartbeat on all channels")
    return False
