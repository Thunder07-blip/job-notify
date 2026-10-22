# Assumptions

This document records all assumptions made during the design and implementation of the
Job Notify system. Review these if the system behaves unexpectedly.

## Portal & API Assumptions

1. **Sample data reconstructed**: The provided `samples/results.json` was empty. A realistic
   sample (`samples/response.json`) was created based on the field descriptions in the spec.
   The actual portal response may have additional/different fields — the parser is written
   defensively to handle missing keys gracefully.

2. **`form_start` and `deadline` are NOT in the portal JSON**: The portal API returns
   `regStartdate`, `regStarttime`, `regEnddate`, `regEndtime`. The `form_start` and `deadline`
   fields referenced in the spec are *computed* by combining date+time fields, not returned
   directly by the API. Our `parser.py` builds these from the raw date/time fields.

3. **Date/time format**: `regStartdate` uses `%d-%b-%Y` (e.g., "25-Oct-2026") and
   `regStarttime` / `regEndtime` use `%H:%M` (e.g., "09:00"). If the portal changes formats,
   `parser.py` will raise `PARSE_ERROR`.

4. **Timezone**: All portal datetimes are in Asia/Kolkata (IST, UTC+5:30). There is no DST
   in India, so this is always +05:30.

5. **`regStartdatenew` / `regEnddatenew` ignored**: Per spec, these are UTC midnight
   conversions and lack actual time info. We never use them.

6. **All companies in one response**: The API endpoint
   `TPOCompanyScheduling/newschedulesdcopanies` returns ALL companies in a single JSON
   response, not paginated. If pagination is added later, we'll see fewer companies
   and the `EMPTY_RESULT_SUSPICIOUS` guard will alert us.

7. **Contact fields stripped**: `contact_person`, `contact_email`, `contact_phone` are
   never stored in state. They are dropped during parsing.

8. **`isactive` field**: Boolean. Companies with `isactive: false` are filtered out
   entirely — not stored, not notified.

9. **`tpoprogram` is a comma-separated string** of program names, not an array.

10. **Package fields are display-only strings**: `maxPackage` and `minPackage` can be
    empty strings, numeric strings like "12.5", or messy values like "7.0JS:7".
    We store and display them as-is without parsing.

## Login & Session Assumptions

11. **No CAPTCHA or OTP on login**: The spec confirms this. If one appears, we classify
    it as `LOGIN_BLOCKED` and alert.

12. **One active session per account**: Logging in invalidates any existing session.
    We limit portal polls to configured times (default: every 2 hours, 08:00–22:00 IST)
    to minimize disruption to the user's own browsing.

13. **Login selectors may change**: We use the spec-provided CSS selectors but also
    have fallback selectors for input fields (`input[type="text"]`, `input[type="password"]`)
    in case IDs change (Vuetify generates dynamic IDs). If all selectors fail, we
    raise `LOGIN_FAILED`.

14. **Session Already Active page**: If detected, we wait 2 minutes and retry once.
    A second occurrence is a failure.

15. **Logout mechanism**: We attempt to click a logout button/link on the dashboard.
    If no logout element is found, we simply close the browser — the next login will
    invalidate the session anyway.

## Notification Assumptions

16. **Gmail SMTP with app password**: Uses `smtp.gmail.com:465` with SSL. The user
    must enable 2-Step Verification on their Google account and create an App Password.

17. **Telegram Bot API**: Uses HTTPS `api.telegram.org`. The bot must be started by
    the user (send `/start` to the bot) before it can send messages.

18. **ICS calendar files**: Generated using only the standard library (no `icalendar`
    package). We produce RFC 5545-compliant iCalendar data. Some calendar clients may
    not process METHOD:REQUEST without a proper ORGANIZER — we set a generic organizer.

19. **Idempotency**: Notification deduplication is tracked per-channel in
    `state/state.json`. Running twice produces zero duplicate sends.

20. **First run is silent by default**: To avoid spamming with 50+ existing companies.
    Use `--announce-existing` to override.

## Infrastructure Assumptions

21. **GitHub Actions free tier**: Provides 2,000 minutes/month for private repos,
    unlimited for public repos. At ~2 minutes per run × 48 runs/day = ~96 min/day
    ≈ 2,880 min/month. **The repo should be public** to stay within free tier, or
    the schedule should be reduced for private repos.

22. **GitHub disables scheduled workflows after 60 days of inactivity**: We commit
    `state/state.json` with `last_success_at` updates to keep the repo active. The
    heartbeat also ensures weekly activity.

23. **Playwright in GitHub Actions**: Uses headless Chromium. We cache the browser
    binary to speed up runs. The `ubuntu-latest` runner has sufficient memory.

24. **GitHub Actions cron is not precise**: Runs may be delayed by minutes. We use
    a grace window (configurable, default 35 minutes) to handle delays.

## Dependencies

25. **playwright**: Required for browser automation. No alternative meets the
    "browser-based fetch only" requirement. Pinned in `requirements.txt`.

26. **pytest**: Required for testing. Standard choice.

27. **No other third-party packages**: Everything else uses the Python standard library:
    - `zoneinfo` for timezone handling (Python 3.9+)
    - `smtplib` + `email` for sending emails
    - `json` for state management
    - `urllib.request` for Telegram Bot API calls
    - `hashlib`, `uuid` for ICS UIDs
    - `argparse` for CLI flags
    - `pathlib`, `tempfile`, `os` for file operations

## Configuration Defaults

28. **PROGRAM_FILTERS**: `["VIT-BTech-Computer Engineering"]` — configurable via
    environment variable `PROGRAM_FILTERS` (comma-separated).

29. **POLL_TIMES_IST**: Every 2 hours from 08:00 to 22:00 IST — configurable via
    environment variable `POLL_TIMES_IST`.

30. **Heartbeat interval**: Weekly (every 7 days).

31. **State pruning**: Companies are removed 30 days after their deadline.

32. **Reminder windows**: 24 hours and 2 hours before deadline.

33. **Grace window for poll scheduling**: 35 minutes (slightly longer than the
    30-minute cron interval).

## Known Limitations

34. **Portal IP blocking**: If the portal blocks GitHub Actions IP ranges, the
    system will fail with network errors. Workaround: run locally with a systemd
    timer (documented in TROUBLESHOOTING.md).

35. **Portal UI changes**: If Vuetify version or page structure changes, login
    selectors may break. We have fallbacks but they're not guaranteed.

36. **Single recipient**: Email and Telegram notifications go to one user. Adding
    multiple recipients requires code changes.

37. **No WhatsApp, no web UI, no database**: Per spec, these are out of scope.
