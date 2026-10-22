# Setup Guide

A beginner-friendly guide to setting up the Job Notify system from scratch.
Estimated time: **30–45 minutes**.

---

## Prerequisites

- A GitHub account
- A Telegram account
- A Gmail account with 2-Step Verification enabled

---

## Step 1: Fork the Repository

1. Go to the repository on GitHub.
2. Click **Fork** (top right corner).
3. Choose your account as the destination.

> **Important**: For the GitHub Actions free tier, keep the repo **public**. Private repos
> have a 2,000 minutes/month limit which may not be enough for 24/7 monitoring.

---

## Step 2: Create a Telegram Bot

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot`.
3. Choose a name (e.g., "My Placement Notifier").
4. Choose a username (must end in `bot`, e.g., `my_placement_bot`).
5. BotFather will give you a **Bot Token** that looks like `1234567890:AABBccDDeeff...`.
   **Save this** – it's your `TELEGRAM_BOT_TOKEN`.

### Get your Chat ID

1. Start a chat with your new bot: search for it in Telegram and send `/start`.
2. Open this URL in your browser (replace `YOUR_BOT_TOKEN`):
   ```
   https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
   ```
3. Look for `"chat":{"id":` in the response. That number is your `TELEGRAM_CHAT_ID`.
   It looks like `123456789`.

---

## Step 3: Generate a Gmail App Password

> You must have 2-Step Verification enabled on your Google account first.

1. Go to [myaccount.google.com/security](https://myaccount.google.com/security).
2. Under "How you sign in to Google", click **2-Step Verification**.
3. Scroll to the bottom and click **App passwords**.
4. Select **Mail** and **Other (Custom name)**, type `PlacementBot`, click **Generate**.
5. Google shows a 16-character password like `abcd efgh ijkl mnop`.
   **Copy it immediately** (it won't be shown again).
   Remove the spaces: `abcdefghijklmnop` – this is your `GMAIL_APP_PASSWORD`.

---

## Step 4: Configure GitHub Secrets

In your forked repository:

1. Go to **Settings** → **Secrets and variables** → **Actions**.
2. Click **New repository secret** for each of the following:

| Secret Name | Value |
|-------------|-------|
| `PORTAL_USERNAME` | Your portal login (e.g., `12345678@vit.edu`) |
| `PORTAL_PASSWORD` | Your portal password |
| `TELEGRAM_BOT_TOKEN` | Token from Step 2 |
| `TELEGRAM_CHAT_ID` | Your chat ID from Step 2 |
| `GMAIL_ADDRESS` | Your Gmail address (e.g., `you@gmail.com`) |
| `GMAIL_APP_PASSWORD` | 16-character app password from Step 3 |
| `NOTIFY_EMAIL_TO` | Email to receive notifications (can be same as GMAIL_ADDRESS) |

> **Security**: Never put these values directly in any file. GitHub Secrets are the only
> safe place. They are masked in all workflow logs.

---

## Step 5: Local Dry Run (Optional but Recommended)

Test everything locally before enabling the workflow:

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/job-notify.git
cd job-notify

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser
playwright install chromium

# Set environment variables (Linux/Mac)
export PORTAL_USERNAME="your_username"
export PORTAL_PASSWORD="your_password"
export TELEGRAM_BOT_TOKEN="your_token"
export TELEGRAM_CHAT_ID="your_chat_id"
export GMAIL_ADDRESS="you@gmail.com"
export GMAIL_APP_PASSWORD="your_app_password"
export NOTIFY_EMAIL_TO="you@gmail.com"
export PYTHONPATH=src

# Dry run (no actual sends, no state changes)
python -m notifier.main --dry-run --force-poll

# Test notifications (sends real messages to email + Telegram)
python -m notifier.main --test-notify
```

---

## Step 6: Run with Local Debug (Headed Browser)

To see the browser actually log in (useful for troubleshooting):

```bash
python -m notifier.main --local-debug --dry-run --force-poll
```

This opens a visible Chromium window showing the login and dashboard.

---

## Step 7: Run Tests

```bash
PYTHONPATH=src python -m pytest tests/ -v
```

All 135 tests should pass.

---

## Step 8: Enable GitHub Actions

1. In your forked repo, go to **Actions**.
2. If you see "Workflows disabled for this fork", click **Enable**.
3. The `Portal Check` workflow will now run automatically every 30 minutes.

---

## Step 9: First Manual Dispatch

Do a test run manually before waiting for the schedule:

1. Go to **Actions** → **Portal Check**.
2. Click **Run workflow** (top right).
3. Set `force_poll` to `true`.
4. Click **Run workflow**.

Watch the run complete. If successful:
- The portal will be logged into.
- Companies will be fetched.
- State will be committed to the repo.
- You'll receive test notifications on your configured channels.

> **First run note**: Companies are seeded silently by default (no notification flood).
> To announce all current companies, add `--announce-existing` to the run command in
> the workflow, or manually dispatch with `force_poll=true` after setting `ANNOUNCE_EXISTING=true`.

---

## Step 10: Verify Everything Works

After the first run:

1. Check `state/state.json` in the repo – it should be updated with companies.
2. Check your Telegram for a heartbeat (may take up to a week).
3. Wait for the next scheduled slot (08:00–22:00 IST, every 2 hours) for a real poll.

---

## Telegram Commands

Send these to your bot at any time:

| Command | Effect |
|---------|--------|
| `/status` | Show current state, last poll, next poll, company count |
| `/pause 4` | Pause portal polling for 4 hours (max 12h) |
| `/resume` | Resume portal polling |

---

## Configuration (Optional)

Override defaults with environment variables in your workflow secrets:

| Variable | Default | Description |
|----------|---------|-------------|
| `PROGRAM_FILTERS` | `VIT-BTech-Computer Engineering` | Comma-separated programs to monitor. Empty = all. |
| `POLL_TIMES_IST` | `08:00,10:00,...,22:00` | Comma-separated IST times to poll. |
| `POLL_GRACE_WINDOW_SECONDS` | `2100` (35 min) | Grace window for delayed GitHub crons. |
| `HEARTBEAT_INTERVAL_HOURS` | `168` (7 days) | How often to send the alive heartbeat. |
