"""
portal.py – Playwright-based browser fetch.

CRITICAL DESIGN: We log in through the real login UI, navigate to the dashboard,
and CAPTURE the response to the API endpoint using page.expect_response.
We NEVER call the API directly, forge signatures, or extract tokens.

Session handling rules:
- One login attempt per poll, one retry for network/timeout only.
- Explicit logout in try/finally.
- Detect SESSION_ACTIVE, CAPTCHA, OTP.
- Generous timeouts for slow portal loads.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from . import config

logger = logging.getLogger(__name__)


# ── Failure Classes ───────────────────────────────────────────────────────────
class PortalError(Exception):
    """Base class for portal fetch errors."""
    def __init__(self, failure_class: str, message: str):
        self.failure_class = failure_class
        super().__init__(f"[{failure_class}] {message}")


class LoginFailed(PortalError):
    def __init__(self, message: str = "Login failed"):
        super().__init__("LOGIN_FAILED", message)


class LoginBlocked(PortalError):
    def __init__(self, message: str = "Login blocked by CAPTCHA/OTP"):
        super().__init__("LOGIN_BLOCKED", message)


class SessionPage(PortalError):
    def __init__(self, message: str = "Session already active"):
        super().__init__("SESSION_PAGE", message)


class ResponseNotCaptured(PortalError):
    def __init__(self, message: str = "API response not captured"):
        super().__init__("RESPONSE_NOT_CAPTURED", message)


class ParseError(PortalError):
    def __init__(self, message: str = "Failed to parse response"):
        super().__init__("PARSE_ERROR", message)


class EmptyResultSuspicious(PortalError):
    def __init__(self, message: str = "Got zero companies but expected many"):
        super().__init__("EMPTY_RESULT_SUSPICIOUS", message)


def fetch_companies(
    *,
    headed: bool = False,
    previous_company_count: int = 0,
) -> list[dict[str, Any]]:
    """
    Log in to the portal, navigate to dashboard, capture the API response,
    and return the raw company_list JSON.

    Args:
        headed: If True, run browser in headed mode (for local debugging).
        previous_company_count: Number of companies in the previous state,
            used for the EMPTY_RESULT_SUSPICIOUS check.

    Returns:
        The raw company_list from the portal API response.

    Raises:
        PortalError subclass on any failure.
    """
    # Import playwright here so tests can mock it
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

    company_list: Optional[list[dict[str, Any]]] = None
    last_error: Optional[Exception] = None

    for attempt in range(1, config.MAX_FETCH_RETRIES + 1):
        browser = None
        context = None
        page = None

        try:
            logger.info("Portal fetch attempt %d/%d", attempt, config.MAX_FETCH_RETRIES)

            pw = sync_playwright().start()
            try:
                browser = pw.chromium.launch(
                    headless=not headed,
                    args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"],
                )
                context = browser.new_context(
                    user_agent=config.USER_AGENT,
                    viewport={"width": 1280, "height": 720},
                )
                page = context.new_page()

                # Step 1: Navigate to login page
                logger.info("Navigating to login page")
                page.goto(config.PORTAL_LOGIN_URL, timeout=config.BROWSER_TIMEOUT_MS)
                page.wait_for_load_state("networkidle", timeout=config.BROWSER_TIMEOUT_MS)

                # Step 2: Check for blockers (CAPTCHA, OTP, SSO)
                _check_for_blockers(page)

                # Step 3: Check for "Session Already Active" page
                _check_session_active(page)

                # Step 4: Fill login form
                _fill_login(page)

                # Step 5: Set up response capture BEFORE navigating to dashboard
                # We listen for the API call that the dashboard's JS makes
                captured_response = {"data": None}

                def handle_response(response):
                    """Capture the API response when it arrives."""
                    if config.PORTAL_API_URL in response.url:
                        try:
                            captured_response["data"] = response.json()
                            logger.info("Captured API response from %s", response.url)
                        except Exception as e:
                            logger.warning("Failed to read API response JSON: %s", e)

                page.on("response", handle_response)

                # Step 6: Navigate to dashboard (or check if we're already there)
                logger.info("Navigating to dashboard")
                try:
                    # In Vue SPA, clicking the sidebar link performs smooth client-side routing
                    page.wait_for_selector('a[href="/company-dashboard"]', timeout=10_000, state="attached")
                    page.click('a[href="/company-dashboard"]', force=True)
                    logger.debug("Clicked dashboard nav item")
                except Exception as e:
                    logger.warning("Could not click sidebar link, falling back to goto: %s", e)
                    page.goto(config.PORTAL_DASHBOARD_URL, timeout=config.BROWSER_TIMEOUT_MS)

                try:
                    page.wait_for_load_state("networkidle", timeout=config.RESPONSE_TIMEOUT_MS)
                except Exception:
                    pass

                # Step 7: Check if we're actually on the dashboard (login might have failed)
                _verify_on_dashboard(page)

                # Step 8: Wait a moment for the API call to complete if not captured yet
                if captured_response["data"] is None:
                    logger.info("Waiting for API response...")
                    # Give it extra time – the JS may fire the API call after page load
                    for _ in range(10):
                        if captured_response["data"] is not None:
                            break
                        page.wait_for_timeout(1000)

                # Step 9: Extract company_list
                if captured_response["data"] is None:
                    raise ResponseNotCaptured(
                        "Dashboard loaded but API response was not captured. "
                        "The portal may have changed its API endpoint."
                    )

                raw = captured_response["data"]
                if not isinstance(raw, dict):
                    raise ParseError("API response is not a JSON object")

                company_list_raw = raw.get("company_list")
                if company_list_raw is None:
                    raise ParseError("API response missing 'company_list'")

                if not isinstance(company_list_raw, list):
                    raise ParseError("'company_list' is not an array")

                # Step 10: EMPTY_RESULT_SUSPICIOUS check
                if (len(company_list_raw) == 0
                        and previous_company_count > 5):
                    raise EmptyResultSuspicious(
                        f"Got 0 companies but state had {previous_company_count}. "
                        "Treating as failure to avoid removing all companies."
                    )

                company_list = company_list_raw
                logger.info("Successfully fetched %d companies", len(company_list))
                break  # success – exit retry loop

            finally:
                # ALWAYS log out and close browser, even on failure
                try:
                    if page and not page.is_closed():
                        _logout(page)
                except Exception as e:
                    logger.warning("Logout failed: %s", e)
                try:
                    if context:
                        context.close()
                except Exception:
                    pass
                try:
                    if browser:
                        browser.close()
                except Exception:
                    pass
                try:
                    pw.stop()
                except Exception:
                    pass

        except (LoginFailed, LoginBlocked, ParseError, EmptyResultSuspicious):
            # These are not transient – don't retry
            raise
        except SessionPage as e:
            if attempt == 1:
                logger.warning("Session page detected, waiting 2 minutes before retry")
                time.sleep(config.LOGIN_RETRY_DELAY_SECONDS)
                last_error = e
                continue
            raise
        except PwTimeout as e:
            last_error = e
            if attempt < config.MAX_FETCH_RETRIES:
                delay = config.LOGIN_RETRY_DELAY_SECONDS * attempt
                logger.warning("Timeout on attempt %d, retrying in %ds: %s",
                               attempt, delay, e)
                time.sleep(delay)
            continue
        except Exception as e:
            last_error = e
            if attempt < config.MAX_FETCH_RETRIES:
                delay = config.LOGIN_RETRY_DELAY_SECONDS * attempt
                logger.warning("Error on attempt %d, retrying in %ds: %s",
                               attempt, delay, e)
                time.sleep(delay)
            continue

    if company_list is None:
        raise ResponseNotCaptured(
            f"Failed to fetch companies after {config.MAX_FETCH_RETRIES} attempts. "
            f"Last error: {last_error}"
        )

    return company_list


def _fill_login(page) -> None:
    """Fill in the login form and submit."""
    if not config.PORTAL_USERNAME or not config.PORTAL_PASSWORD:
        raise LoginFailed("Portal credentials not configured (env vars empty)")

    # Try username selectors
    username_filled = False
    for selector in config.USERNAME_SELECTORS:
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                el.click()
                el.fill(config.PORTAL_USERNAME)
                username_filled = True
                logger.debug("Username filled using selector: %s", selector)
                break
        except Exception:
            continue

    if not username_filled:
        raise LoginFailed("Could not find username input field")

    # Try password selectors
    password_filled = False
    for selector in config.PASSWORD_SELECTORS:
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                el.click()
                el.fill(config.PORTAL_PASSWORD)
                password_filled = True
                logger.debug("Password filled using selector: %s", selector)
                break
        except Exception:
            continue

    if not password_filled:
        raise LoginFailed("Could not find password input field")

    # Submit
    submitted = False
    for selector in config.SUBMIT_SELECTORS:
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                el.click()
                submitted = True
                logger.debug("Login submitted using selector: %s", selector)
                break
        except Exception:
            continue

    if not submitted:
        raise LoginFailed("Could not find login submit button")

    # Wait for navigation after login (portal redirects to /home)
    try:
        page.wait_for_url("**/home*", timeout=15_000)
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=config.BROWSER_TIMEOUT_MS)
    except Exception:
        pass


def _check_for_blockers(page) -> None:
    """
    Detect CAPTCHA, OTP, or SSO elements that would block automated login.
    If found, raise LoginBlocked.
    """
    blocker_indicators = [
        "captcha",
        "recaptcha",
        "g-recaptcha",
        "hcaptcha",
        "otp",
        "one-time password",
        "verification code",
        "sso",
        "single sign-on",
    ]

    page_text = page.text_content("body") or ""
    page_text_lower = page_text.lower()

    for indicator in blocker_indicators:
        if indicator in page_text_lower:
            raise LoginBlocked(
                f"Detected possible access control: '{indicator}' found on login page"
            )

    # Check for reCAPTCHA iframe
    recaptcha_frame = page.query_selector("iframe[src*='recaptcha']")
    if recaptcha_frame:
        raise LoginBlocked("reCAPTCHA iframe detected on login page")


def _check_session_active(page) -> None:
    """Detect the 'Session Already Active' page."""
    page_text = page.text_content("body") or ""
    for marker in config.SESSION_ACTIVE_STRINGS:
        if marker.lower() in page_text.lower():
            raise SessionPage(f"Detected '{marker}' on page")


def _verify_on_dashboard(page) -> None:
    """
    Verify that login succeeded and we're on the dashboard, not still on
    the login page.
    """
    current_url = page.url
    page_text = (page.text_content("body") or "").lower()

    # Check for session-active after login
    for marker in config.SESSION_ACTIVE_STRINGS:
        if marker.lower() in page_text:
            raise SessionPage(f"Detected '{marker}' after login attempt")

    # If URL is still the login page and not the dashboard or home
    if "company-dashboard" not in current_url and "/home" not in current_url:
        for selector in config.PASSWORD_SELECTORS:
            el = page.query_selector(selector)
            if el and el.is_visible():
                raise LoginFailed(
                    "Still on login page after submit – credentials may be wrong"
                )


def _logout(page) -> None:
    """
    Attempt to log out by clicking a logout button/link.
    If no logout element found, just return – closing the browser will end the session.
    """
    logout_selectors = [
        "a[href*='logout']",
        "button:has-text('Logout')",
        "button:has-text('Sign Out')",
        "button:has-text('Log Out')",
        ".logout-btn",
        "#logout",
        "[data-testid='logout']",
    ]

    for selector in logout_selectors:
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                el.click()
                logger.info("Logged out successfully")
                page.wait_for_timeout(1000)
                return
        except Exception:
            continue

    # Try finding logout in user menu/dropdown first
    menu_selectors = [
        ".v-avatar",
        ".user-menu",
        ".v-list-item:has-text('Logout')",
    ]
    for selector in menu_selectors:
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                el.click()
                page.wait_for_timeout(500)
                # Now try logout selectors again
                for logout_sel in logout_selectors:
                    try:
                        el2 = page.query_selector(logout_sel)
                        if el2 and el2.is_visible():
                            el2.click()
                            logger.info("Logged out via menu")
                            return
                    except Exception:
                        continue
        except Exception:
            continue

    logger.info("No logout button found – browser close will end session")
