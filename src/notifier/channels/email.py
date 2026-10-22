"""
email.py – Email notification channel via Gmail SMTP.

Sends plain text + HTML emails with .ics calendar event attachments.
Uses smtp.gmail.com:465 with SSL and a Gmail app password.
"""

from __future__ import annotations

import hashlib
import logging
import re
import smtplib
import ssl
from datetime import datetime, timedelta
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from typing import Optional

from .. import config
from ..models import Company, CompanyChange

logger = logging.getLogger(__name__)


def _parse_recipients(raw: str) -> list[str]:
    """Parse comma, semicolon, space, or 'and'-separated email addresses."""
    cleaned = re.sub(r"\s+and\s+", ",", raw, flags=re.IGNORECASE)
    tokens = re.split(r"[,;\s]+", cleaned)
    return [t.strip() for t in tokens if t.strip() and "@" in t]


def send_new_company(company: Company) -> bool:
    """Send notification for a newly detected company."""
    subject = _build_subject(company)
    plain = _build_new_plain(company)
    html = _build_new_html(company)
    ics = generate_ics(company, sequence=0)
    return _send_email(subject, plain, html, ics)


def send_change(change: CompanyChange) -> bool:
    """Send notification for a changed start time or deadline."""
    company = change.company
    subject = f"[Placement Update] {company.company} - {change.change_type.replace('_', ' ').title()}"
    plain = _build_change_plain(change)
    html = _build_change_html(change)
    # Increment sequence for calendar updates
    ics = generate_ics(company, sequence=1)
    return _send_email(subject, plain, html, ics)


def send_reminder(company: Company, reminder_type: str) -> bool:
    """Send a deadline reminder (24h or 2h)."""
    if reminder_type == "reminder_24h":
        time_label = "24 hours"
    elif reminder_type == "reminder_2h":
        time_label = "2 hours"
    else:
        time_label = reminder_type

    subject = f"[Placement Reminder] {company.company} - Deadline in {time_label}!"
    plain = _build_reminder_plain(company, time_label)
    html = _build_reminder_html(company, time_label)
    return _send_email(subject, plain, html)


def send_registration_open(company: Company) -> bool:
    """Send notification that registration is now open."""
    subject = f"[Placement] {company.company} - Registration is NOW OPEN!"
    plain = _build_reg_open_plain(company)
    html = _build_reg_open_html(company)
    return _send_email(subject, plain, html)


def send_alert(message: str) -> bool:
    """Send an alert message (scraper broken, login failed, etc.)."""
    subject = "[Placement Alert] System Issue"
    return _send_email(subject, message, f"<html><body><pre>{_esc(message)}</pre></body></html>")


def send_recovery(message: str) -> bool:
    """Send a recovery notification."""
    subject = "[Placement] System Recovered"
    return _send_email(subject, message, f"<html><body><p>{_esc(message)}</p></body></html>")


def send_heartbeat(message: str) -> bool:
    """Send a weekly heartbeat."""
    subject = "[Placement] Weekly Heartbeat"
    return _send_email(subject, message, f"<html><body><p>{_esc(message)}</p></body></html>")


def send_test() -> bool:
    """Send a test notification to verify email configuration."""
    subject = "[Placement] Test Notification"
    body = "This is a test notification from the Job Notify system. If you received this, email is configured correctly!"
    return _send_email(subject, body, f"<html><body><p>{_esc(body)}</p></body></html>")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _send_email(
    subject: str,
    plain_body: str,
    html_body: str,
    ics_content: Optional[str] = None,
) -> bool:
    """
    Send an email via Gmail SMTP.

    Returns True on success, False on failure (logged, not raised).
    """
    recipients = _parse_recipients(config.NOTIFY_EMAIL_TO)
    if not config.GMAIL_ADDRESS or not config.GMAIL_APP_PASSWORD or not recipients:
        logger.error("Email not configured: missing GMAIL_ADDRESS, GMAIL_APP_PASSWORD, or valid NOTIFY_EMAIL_TO")
        return False

    try:
        msg = MIMEMultipart("mixed")
        msg["From"] = config.GMAIL_ADDRESS
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject

        # Create alternative part for plain+HTML
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(plain_body, "plain", "utf-8"))
        alt.attach(MIMEText(html_body, "html", "utf-8"))
        msg.attach(alt)

        # Attach .ics file if provided
        if ics_content:
            ics_part = MIMEBase("text", "calendar", method="REQUEST")
            ics_part.set_payload(ics_content.encode("utf-8"))
            encoders.encode_base64(ics_part)
            ics_part.add_header("Content-Disposition", "attachment", filename="event.ics")
            msg.attach(ics_part)

        # Send via SSL
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as server:
            server.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
            server.sendmail(config.GMAIL_ADDRESS, recipients, msg.as_string())

        logger.info("Email sent to %s: %s", ", ".join(recipients), subject)
        return True

    except Exception as e:
        logger.error("Failed to send email '%s': %s", subject, e)
        return False


def _build_subject(company: Company) -> str:
    """Build email subject for a new company notification."""
    deadline_str = ""
    if company.deadline:
        deadline_str = f" - Registration closes {company.deadline.strftime('%d %b %Y %H:%M IST')}"
    return f"[Placement] {company.company}{deadline_str}"


def _build_new_plain(company: Company) -> str:
    """Build plain text body for a new company notification."""
    lines = [
        f"New Company: {company.company}",
        f"Type: {company.placementtype}",
        f"Package: {company.package_display()}",
        f"Programs: {company.tpoprogram}",
        f"Registration Start: {_fmt_dt(company.form_start)}",
        f"Deadline: {_fmt_dt(company.deadline)}",
        f"Academic Year: {company.academicyear}",
        "",
        "Register on the portal: https://tpo.vierp.in/company-dashboard",
    ]
    return "\n".join(lines)


def _build_new_html(company: Company) -> str:
    """Build HTML body for a new company notification."""
    return f"""<html><body>
<h2>🏢 New Company: {_esc(company.company)}</h2>
<table style="border-collapse:collapse;font-family:sans-serif;">
<tr><td style="padding:4px 12px;font-weight:bold;">Type</td><td style="padding:4px 12px;">{_esc(company.placementtype)}</td></tr>
<tr><td style="padding:4px 12px;font-weight:bold;">Package</td><td style="padding:4px 12px;">{_esc(company.package_display())}</td></tr>
<tr><td style="padding:4px 12px;font-weight:bold;">Programs</td><td style="padding:4px 12px;">{_esc(company.tpoprogram)}</td></tr>
<tr><td style="padding:4px 12px;font-weight:bold;">Registration Start</td><td style="padding:4px 12px;">{_esc(_fmt_dt(company.form_start))}</td></tr>
<tr><td style="padding:4px 12px;font-weight:bold;">Deadline</td><td style="padding:4px 12px;">{_esc(_fmt_dt(company.deadline))}</td></tr>
<tr><td style="padding:4px 12px;font-weight:bold;">Academic Year</td><td style="padding:4px 12px;">{_esc(company.academicyear)}</td></tr>
</table>
<p><a href="https://tpo.vierp.in/company-dashboard">Register on the portal →</a></p>
</body></html>"""


def _build_change_plain(change: CompanyChange) -> str:
    """Build plain text for a change notification."""
    c = change.company
    field = "Registration Start" if change.change_type == "start_changed" else "Deadline"
    return "\n".join([
        f"Company: {c.company}",
        f"Change: {field}",
        f"Before: {change.old_value or 'Not set'}",
        f"After: {change.new_value or 'Not set'}",
        "",
        "Check the portal: https://tpo.vierp.in/company-dashboard",
    ])


def _build_change_html(change: CompanyChange) -> str:
    """Build HTML for a change notification."""
    c = change.company
    field = "Registration Start" if change.change_type == "start_changed" else "Deadline"
    return f"""<html><body>
<h2>📝 Update: {_esc(c.company)}</h2>
<p><strong>{field} Changed</strong></p>
<p>Before: <s>{_esc(change.old_value or 'Not set')}</s></p>
<p>After: <strong>{_esc(change.new_value or 'Not set')}</strong></p>
<p><a href="https://tpo.vierp.in/company-dashboard">Check the portal →</a></p>
</body></html>"""


def _build_reminder_plain(company: Company, time_label: str) -> str:
    """Build plain text for a deadline reminder."""
    return "\n".join([
        f"⏰ Deadline Reminder: {company.company}",
        f"Deadline: {_fmt_dt(company.deadline)}",
        f"Time remaining: {time_label}",
        f"Type: {company.placementtype}",
        "",
        "Register now: https://tpo.vierp.in/company-dashboard",
    ])


def _build_reminder_html(company: Company, time_label: str) -> str:
    """Build HTML for a deadline reminder."""
    return f"""<html><body>
<h2>⏰ Deadline in {_esc(time_label)}: {_esc(company.company)}</h2>
<p><strong>Deadline:</strong> {_esc(_fmt_dt(company.deadline))}</p>
<p><strong>Type:</strong> {_esc(company.placementtype)}</p>
<p style="color:red;font-weight:bold;">Register now before it's too late!</p>
<p><a href="https://tpo.vierp.in/company-dashboard">Register on the portal →</a></p>
</body></html>"""


def _build_reg_open_plain(company: Company) -> str:
    """Build plain text for registration-open notification."""
    return "\n".join([
        f"✅ Registration is NOW OPEN: {company.company}",
        f"Type: {company.placementtype}",
        f"Package: {company.package_display()}",
        f"Deadline: {_fmt_dt(company.deadline)}",
        "",
        "Register now: https://tpo.vierp.in/company-dashboard",
    ])


def _build_reg_open_html(company: Company) -> str:
    """Build HTML for registration-open notification."""
    return f"""<html><body>
<h2>✅ Registration OPEN: {_esc(company.company)}</h2>
<p><strong>Type:</strong> {_esc(company.placementtype)}</p>
<p><strong>Package:</strong> {_esc(company.package_display())}</p>
<p><strong>Deadline:</strong> {_esc(_fmt_dt(company.deadline))}</p>
<p><a href="https://tpo.vierp.in/company-dashboard">Register NOW →</a></p>
</body></html>"""


def generate_ics(company: Company, sequence: int = 0) -> str:
    """
    Generate an RFC 5545 iCalendar file with:
    - VEVENT for registration opening (if form_start is known).
    - VEVENT for deadline (if deadline is known).
    - VALARMs 1 day and 2 hours before deadline.
    - Stable UID per company+event type.
    - Incremented SEQUENCE for updates.

    Uses METHOD:REQUEST so calendar clients treat it as an invitation.
    """
    events = []

    # UID generation: stable hash based on company ID
    base_uid = hashlib.md5(f"job-notify-{company.id}".encode()).hexdigest()

    if company.form_start:
        uid_start = f"{base_uid}-start@job-notify"
        events.append(_build_vevent(
            uid=uid_start,
            summary=f"Registration Opens: {company.company}",
            dtstart=company.form_start,
            dtend=company.form_start + timedelta(hours=1),
            description=f"Type: {company.placementtype}\nPackage: {company.package_display()}",
            sequence=sequence,
            alarms=[],
        ))

    if company.deadline:
        uid_end = f"{base_uid}-deadline@job-notify"
        alarms = [
            timedelta(days=-1),    # 1 day before
            timedelta(hours=-2),   # 2 hours before
        ]
        events.append(_build_vevent(
            uid=uid_end,
            summary=f"Registration Deadline: {company.company}",
            dtstart=company.deadline - timedelta(hours=1),
            dtend=company.deadline,
            description=f"Type: {company.placementtype}\nPackage: {company.package_display()}\nDEADLINE!",
            sequence=sequence,
            alarms=alarms,
        ))

    if not events:
        return ""

    cal_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//JobNotify//Placement Monitor//EN",
        "METHOD:REQUEST",
        "CALSCALE:GREGORIAN",
    ]
    for event in events:
        cal_lines.extend(event)
    cal_lines.append("END:VCALENDAR")

    return "\r\n".join(cal_lines) + "\r\n"


def _build_vevent(
    uid: str,
    summary: str,
    dtstart: datetime,
    dtend: datetime,
    description: str,
    sequence: int,
    alarms: list[timedelta],
) -> list[str]:
    """Build VEVENT lines for an ICS file."""
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTART:{_ics_datetime(dtstart)}",
        f"DTEND:{_ics_datetime(dtend)}",
        f"SUMMARY:{_ics_escape(summary)}",
        f"DESCRIPTION:{_ics_escape(description)}",
        f"SEQUENCE:{sequence}",
        f"DTSTAMP:{_ics_datetime(datetime.now(config.IST))}",
        "ORGANIZER:mailto:noreply@job-notify.local",
        "STATUS:CONFIRMED",
    ]

    for alarm_delta in alarms:
        lines.extend([
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:Reminder: {summary}",
            f"TRIGGER:{_ics_duration(alarm_delta)}",
            "END:VALARM",
        ])

    lines.append("END:VEVENT")
    return lines


def _ics_datetime(dt: datetime) -> str:
    """Format datetime for ICS (UTC)."""
    utc_dt = dt.astimezone(config.IST)
    return utc_dt.strftime("%Y%m%dT%H%M%S")


def _ics_duration(td: timedelta) -> str:
    """Format a timedelta as an ICS duration trigger."""
    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        sign = "-"
        total_seconds = abs(total_seconds)
    else:
        sign = ""

    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60

    parts = [f"{sign}P"]
    if days:
        parts.append(f"{days}D")
    if hours or minutes:
        parts.append("T")
        if hours:
            parts.append(f"{hours}H")
        if minutes:
            parts.append(f"{minutes}M")

    return "".join(parts)


def _ics_escape(text: str) -> str:
    """Escape special characters for ICS text fields."""
    return text.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def _fmt_dt(dt: Optional[datetime]) -> str:
    """Format a datetime for display."""
    if dt is None:
        return "Not set"
    return dt.strftime("%d %b %Y %H:%M IST")


def _esc(text: str) -> str:
    """Escape HTML special characters."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))
