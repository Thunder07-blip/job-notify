"""
config.py – Central configuration for the notifier system.

All secrets come from environment variables. All defaults are safe.
Configuration is read once at import time and is immutable thereafter.
"""

import os
from zoneinfo import ZoneInfo

# ── Timezone ──────────────────────────────────────────────────────────────────
IST = ZoneInfo("Asia/Kolkata")

# ── Portal URLs ───────────────────────────────────────────────────────────────
PORTAL_LOGIN_URL = "https://tpo.vierp.in/"
PORTAL_DASHBOARD_URL = "https://tpo.vierp.in/company-dashboard"
PORTAL_API_URL = "https://tpoapi.vierp.in/TPOCompanyScheduling/newschedulesdcopanies"

# ── Login Selectors (primary + fallbacks) ─────────────────────────────────────
# Vuetify generates dynamic IDs, so we provide multiple selector strategies.
USERNAME_SELECTORS = [
    "#input-15",                          # spec-provided ID
    "input[type='text']",                 # fallback: first text input
    "input[aria-label*='user' i]",        # aria-label heuristic
    ".v-text-field__slot input",          # Vuetify slot input
]
PASSWORD_SELECTORS = [
    "#input-19",                          # spec-provided ID
    "input[type='password']",             # reliable fallback
]
SUBMIT_SELECTORS = [
    "button.logi",                        # spec-provided class
    "button.v-btn.logi",                  # more specific Vuetify class
    "form button[type='submit']",         # generic form submit
]

# ── Secrets (from environment, NEVER hardcoded) ──────────────────────────────
PORTAL_USERNAME = os.environ.get("PORTAL_USERNAME", "")
PORTAL_PASSWORD = os.environ.get("PORTAL_PASSWORD", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
NOTIFY_EMAIL_TO = os.environ.get("NOTIFY_EMAIL_TO", "")

# ── Program Filtering ────────────────────────────────────────────────────────
# Comma-separated list of program strings to match against tpoprogram.
# Set PROGRAM_FILTERS="" to disable filtering entirely.
_raw_filters = os.environ.get("PROGRAM_FILTERS", "VIT-BTech-Computer Engineering")
PROGRAM_FILTERS: list[str] = [f.strip() for f in _raw_filters.split(",") if f.strip()]
FILTER_ENABLED: bool = bool(PROGRAM_FILTERS)

# ── Poll Schedule (IST) ──────────────────────────────────────────────────────
# Format: comma-separated HH:MM times in IST.
_raw_poll_times = os.environ.get(
    "POLL_TIMES_IST",
    "08:00,10:00,12:00,14:00,16:00,18:00,20:00,22:00"
)
POLL_TIMES_IST: list[str] = [t.strip() for t in _raw_poll_times.split(",") if t.strip()]

# ── Grace window (seconds) for poll scheduling ───────────────────────────────
# Slightly longer than the 30-min cron interval to handle GitHub delays.
POLL_GRACE_WINDOW_SECONDS = int(os.environ.get("POLL_GRACE_WINDOW_SECONDS", "2100"))  # 35 min

# ── Heartbeat ─────────────────────────────────────────────────────────────────
HEARTBEAT_INTERVAL_HOURS = int(os.environ.get("HEARTBEAT_INTERVAL_HOURS", "168"))  # 7 days

# ── Reminder Windows (seconds before deadline) ───────────────────────────────
REMINDER_24H_SECONDS = 24 * 3600
REMINDER_2H_SECONDS = 2 * 3600

# ── State / Pruning ──────────────────────────────────────────────────────────
STATE_FILE = os.environ.get("STATE_FILE", "state/state.json")
PRUNE_AFTER_DAYS = 30

# ── Browser Settings ─────────────────────────────────────────────────────────
BROWSER_TIMEOUT_MS = 30_000       # 30s for page loads
RESPONSE_TIMEOUT_MS = 30_000      # 30s waiting for API response
LOGIN_RETRY_DELAY_SECONDS = 120   # 2 min delay before retrying login
MAX_FETCH_RETRIES = 3

# ── Failure Thresholds ───────────────────────────────────────────────────────
CONSECUTIVE_FAILURES_ALERT = 3    # alert after this many consecutive failures
MAX_LOGIN_FAILURES = 2            # stop polling after this many LOGIN_FAILED

# ── Date Formats ──────────────────────────────────────────────────────────────
DATE_FORMAT = "%d-%b-%Y"          # e.g., "25-Oct-2026"
TIME_FORMAT = "%H:%M"             # e.g., "09:00"
DATETIME_FORMAT = "%d-%b-%Y %H:%M"

# ── Schema Version ────────────────────────────────────────────────────────────
CURRENT_SCHEMA_VERSION = 1

# ── User Agent ────────────────────────────────────────────────────────────────
USER_AGENT = "JobNotifyBot/1.0 (github.com/sajal07/job-notify; student placement monitor)"

# ── Session detection strings ─────────────────────────────────────────────────
SESSION_ACTIVE_STRINGS = [
    "Session Already Active",
    "already have this application open",
]
