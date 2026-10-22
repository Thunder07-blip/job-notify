# Troubleshooting Guide

A reference for every known failure mode, how to diagnose it, and how to fix it.

---

## Failure Classes

### `LOGIN_FAILED`

**Symptom**: The bot is still on the login page after submitting credentials.

**Causes**:
- `PORTAL_USERNAME` or `PORTAL_PASSWORD` secret is wrong or expired.
- The portal login page structure changed and selectors no longer work.

**Fixes**:
1. Verify credentials by logging in manually at https://tpo.vierp.in/
2. Update the `PORTAL_PASSWORD` secret in GitHub Settings → Secrets.
3. After 2 consecutive failures, the system stops polling. Send `/resume` to your
   Telegram bot, or run a manual dispatch with `force_poll=true` after fixing the secret.
4. If selectors changed: check `config.py` `USERNAME_SELECTORS` / `PASSWORD_SELECTORS`
   and update as needed using browser DevTools.

---

### `LOGIN_BLOCKED`

**Symptom**: A CAPTCHA, OTP, or SSO requirement has appeared on the login page.

**Causes**:
- The portal added a CAPTCHA after multiple failed logins.
- The institution switched to SSO (e.g., Google OAuth).

**Fixes**:
1. Log in manually to clear any lockout.
2. If CAPTCHA is now permanent, this tool cannot work without a CAPTCHA-solving
   service (which would be out of scope and against the spec).
3. Contact your TPO office and ask if the portal login method has changed.

---

### `SESSION_PAGE`

**Symptom**: "Session Already Active" message appears on the portal.

**Causes**:
- You (or someone else) is currently logged into the portal.
- A previous bot run crashed without logging out.

**Behavior**:
- The bot waits 2 minutes and retries once.
- If it happens twice, it counts as a failure.

**Fixes**:
1. Wait a few minutes for your existing session to expire.
2. Log out manually from the portal.
3. The bot's next scheduled poll will succeed.

---

### `RESPONSE_NOT_CAPTURED`

**Symptom**: Dashboard loaded but no API response was captured.

**Causes**:
- The portal changed the API endpoint URL from
  `.../TPOCompanyScheduling/newschedulesdcopanies` to something new.
- The dashboard no longer makes an API call on load (e.g., UI was redesigned).

**Fixes**:
1. Open the portal dashboard in your browser with DevTools → Network tab.
2. Look for the new API endpoint that returns a JSON with company data.
3. Update `PORTAL_API_URL` in `config.py`.

---

### `PARSE_ERROR`

**Symptom**: Got an API response but couldn't parse it.

**Causes**:
- The API response format changed (e.g., `company_list` renamed).
- A date format changed from `"%d-%b-%Y"` to something else.

**Fixes**:
1. Check the workflow logs for the actual error message.
2. Compare with `samples/response.json` to see what changed.
3. Update `parser.py` accordingly.

---

### `EMPTY_RESULT_SUSPICIOUS`

**Symptom**: Got 0 companies but state previously had many.

**Causes**:
- The portal returned an empty list due to a temporary error.
- Authentication silently failed and returned empty data.

**Behavior**:
- This is treated as a failure to prevent removing all companies from state.

**Fixes**:
- Usually resolves itself on the next poll.
- If persistent, check the portal manually.

---

## Password Changed

When you change your portal password:

1. Go to GitHub Settings → Secrets → `PORTAL_PASSWORD`.
2. Update the secret with your new password.
3. If the system is in `login_stopped` state, run a manual workflow dispatch with
   `force_poll=true` – this resets the stop state on success.

---

## GitHub Workflow Disabled by Inactivity

**Symptom**: GitHub sends an email "Your workflow has been disabled" or the workflow
stops running after 60 days.

**Why**: GitHub disables scheduled workflows on repositories with no pushes in 60 days.

**Prevention**: The system automatically commits state updates (including `last_success_at`)
weekly, which counts as repository activity. As long as the system is running, it will
prevent itself from being disabled.

**Fix if already disabled**:
1. Go to Actions → Portal Check.
2. Click "Enable workflow".
3. Run a manual dispatch to immediately resume.

---

## Portal Blocks GitHub Actions IPs

**Symptom**: Consistent timeouts or connection errors that don't happen locally.

**Why**: Some institutional portals block traffic from cloud provider IP ranges.

**Running locally instead with a systemd timer**:

1. Clone the repo on your personal computer (must have a stable connection during poll times).

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

3. Create `/etc/systemd/system/job-notify.service`:
   ```ini
   [Unit]
   Description=Job Notify – Portal Monitor

   [Service]
   Type=oneshot
   User=YOUR_USERNAME
   WorkingDirectory=/path/to/job-notify
   Environment="PYTHONPATH=src"
   Environment="PORTAL_USERNAME=your_username"
   Environment="PORTAL_PASSWORD=your_password"
   Environment="TELEGRAM_BOT_TOKEN=your_token"
   Environment="TELEGRAM_CHAT_ID=your_chat_id"
   Environment="GMAIL_ADDRESS=you@gmail.com"
   Environment="GMAIL_APP_PASSWORD=your_app_password"
   Environment="NOTIFY_EMAIL_TO=you@gmail.com"
   ExecStart=/path/to/job-notify/.venv/bin/python -m notifier.main
   ```

4. Create `/etc/systemd/system/job-notify.timer`:
   ```ini
   [Unit]
   Description=Run Job Notify every 30 minutes

   [Timer]
   OnBootSec=1min
   OnUnitActiveSec=30min

   [Install]
   WantedBy=timers.target
   ```

5. Enable:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now job-notify.timer
   ```

6. Check status:
   ```bash
   systemctl status job-notify.timer
   journalctl -u job-notify.service -f
   ```

> **Note**: You'll need to push state changes manually if running locally:
> ```bash
> git add state/state.json && git commit -m "Update state" && git push
> ```
> Or set up git auto-commit in the service.

---

## Workflow Logs Show Credentials

This should never happen – the logging formatter masks all secrets. But if you see
`[REDACTED]` where credentials might be, that's the masker working correctly.

If actual secret values appear, open an issue. Never post real credentials in issues.

---

## Testing Channels Without Real Credentials

```bash
# Tests never use real credentials – they use mocked env vars
PYTHONPATH=src python -m pytest tests/ -v

# Test real channels (uses real secrets from env)
python -m notifier.main --test-notify
```

---

## Getting Help

1. Check the workflow run logs (GitHub Actions → Portal Check → last run).
2. Run locally with `--local-debug --dry-run --force-poll` to see the browser.
3. Open an issue (without including credentials).
