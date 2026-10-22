# Job Notify – College Placement Portal Monitor

Automatically detects new company registrations and deadline changes on your college's
Eduplus placement portal, then sends alerts via **email (with calendar events)** and **Telegram**.
Runs unattended on GitHub Actions at zero cost for ~2 years.

---

## Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│                        GitHub Actions (cron)                       │
│                     Every 30 minutes ("tick")                      │
└─────────────────────────────┬─────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          main.py (Orchestrator)                      │
│                                                                      │
│  1. Load state/state.json                                            │
│  2. Handle Telegram commands (/pause /resume /status)                │
│  3. Is poll due? (POLL_TIMES_IST: day every 2h, night every 4h)      │
│        YES → portal.py (Playwright)                                  │
│           └─ Login → Dashboard → Capture API response               │
│           └─ Parse → Diff → Detect changes                          │
│           └─ Logout (always, even on failure)                       │
│  4. Send due reminders from stored data (no login)                   │
│  5. Send heartbeat if due (weekly)                                   │
│  6. Save state/state.json → commit back                              │
└──────────────────┬───────────────────────────────────────────────────┘
                   │
          ┌────────┴────────┐
          ▼                 ▼
 ┌──────────────┐  ┌─────────────────────┐
 │ channels/    │  │ channels/            │
 │ email.py     │  │ telegram.py          │
 │              │  │                      │
 │ Gmail SMTP   │  │ Bot API (urllib)      │
 │ + .ics file  │  │ + getUpdates polling  │
 └──────────────┘  └─────────────────────┘
```

### Tick vs Poll
- **Tick** (every 30 min): Load state → Telegram commands → reminders → heartbeat → save
- **Poll** (Day: every 2h from 08:00–22:00 IST; Night: every 4h at 02:00, 06:00 IST): Open Chromium → login → capture API response → diff → notify → logout

---

## Module Reference

| File | Purpose |
|------|---------|
| [`config.py`](src/notifier/config.py) | All configuration. Secrets from env vars. Poll times, timeouts, thresholds. |
| [`models.py`](src/notifier/models.py) | `Company` dataclass with serialization. `CompanyChange` for diffs. |
| [`portal.py`](src/notifier/portal.py) | Playwright login + response capture. All error classes. Guaranteed logout. |
| [`parser.py`](src/notifier/parser.py) | Raw API JSON → `list[Company]`. Builds IST datetimes. Strips contact fields. |
| [`state.py`](src/notifier/state.py) | Atomic state R/W. Schema migration. Per-channel notification tracking. |
| [`rules.py`](src/notifier/rules.py) | Company diffing. Reminder scheduling. Poll scheduling. Pause logic. |
| [`channels/email.py`](src/notifier/channels/email.py) | Gmail SMTP with SSL. Plain+HTML+ICS. Stable calendar UIDs. |
| [`channels/telegram.py`](src/notifier/channels/telegram.py) | Bot API sendMessage. HTML escaping. getUpdates command handler. |
| [`heartbeat.py`](src/notifier/heartbeat.py) | Weekly alive notification. |
| [`main.py`](src/notifier/main.py) | Orchestrator. CLI flags. Secret-masking logger. |

---

## Notification Rules

| Trigger | Timing |
|---------|--------|
| **New company** | Immediately when detected |
| **Start/deadline changed** | Immediately on diff |
| **Registration opens** | Once when `now ≥ form_start` |
| **24-hour reminder** | Once when `deadline - now ≤ 24h` |
| **2-hour reminder** | Once when `deadline - now ≤ 2h` |
| **Weekly heartbeat** | Every 7 days |
| **Scraper alert** | After 3 consecutive failed polls |

---

## CLI Flags

```bash
python -m notifier.main                  # normal tick
python -m notifier.main --dry-run        # print without sending
python -m notifier.main --force-poll     # force portal poll now
python -m notifier.main --test-notify    # send test to all channels
python -m notifier.main --announce-existing  # notify all current companies
python -m notifier.main --local-debug    # headed browser for debugging
```

---

## Key Design Decisions

- **Browser-only API access**: Playwright logs in through the real UI and captures the
  API response. No direct API calls, no token extraction.
- **Minimal dependencies**: Only `playwright` and `pytest` are third-party. Everything
  else is stdlib.
- **Idempotent**: Running twice sends zero duplicate notifications.
- **Per-channel tracking**: Email and Telegram are tracked independently so a failure
  on one channel retries on the next run without duplicating on the other.
- **Session respect**: Polls only 9 times per day (08:00–22:00 IST, every 2h) to avoid
  invalidating the student's own session unnecessarily.

---

## Quick Start

1. Fork this repo.
2. Follow [docs/SETUP.md](docs/SETUP.md) to configure secrets.
3. Enable GitHub Actions.
4. Run a manual dispatch with `force_poll=true` to verify everything works.

---

## License

MIT
