"""
parser.py – Parse raw portal JSON into a list[Company].

Responsibilities:
- Extract company_list from the API response.
- Build form_start/deadline datetimes from regStartdate+regStarttime / regEnddate+regEndtime.
- Strip private contact fields.
- Filter out inactive companies.
- Validate required fields.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from . import config
from .models import Company

logger = logging.getLogger(__name__)


class ParseError(Exception):
    """Raised when the portal response cannot be parsed."""
    pass


def parse_response(raw: dict[str, Any]) -> list[Company]:
    """
    Parse the raw JSON response from the portal API into Company objects.

    Args:
        raw: The full JSON response dict from the API endpoint.

    Returns:
        A list of Company objects (only active ones).

    Raises:
        ParseError: If the response structure is unexpected.
    """
    if not isinstance(raw, dict):
        raise ParseError("Response is not a JSON object")

    # The API returns {status: "success", company_list: [...]}
    company_list = raw.get("company_list")
    if company_list is None:
        raise ParseError("Response missing 'company_list' key")

    if not isinstance(company_list, list):
        raise ParseError(f"'company_list' is not an array, got {type(company_list).__name__}")

    companies: list[Company] = []
    for i, item in enumerate(company_list):
        try:
            company = _parse_company(item)
            if company is not None:
                companies.append(company)
        except Exception as e:
            # Log and skip malformed entries rather than failing the whole batch
            company_id = item.get("id", f"index-{i}") if isinstance(item, dict) else f"index-{i}"
            logger.warning("Skipping malformed company entry %s: %s", company_id, e)

    logger.info("Parsed %d active companies from %d total entries", len(companies), len(company_list))
    return companies


def _parse_company(item: dict[str, Any]) -> Optional[Company]:
    """
    Parse a single company dict from the portal response.

    Returns None if the company is inactive (isactive=false).
    Strips contact_* fields for privacy.
    """
    if not isinstance(item, dict):
        raise ParseError(f"Company entry is not a dict: {type(item).__name__}")

    # Required field: id must exist and be an integer
    raw_id = item.get("id")
    if raw_id is None:
        raise ParseError("Company entry missing 'id'")
    company_id = int(raw_id)

    # Filter out inactive companies
    isactive = item.get("isactive", False)
    if not isactive:
        logger.debug("Skipping inactive company id=%d", company_id)
        return None

    # Build form_start and deadline from date+time fields
    form_start = _build_datetime(
        item.get("regStartdate"),
        item.get("regStarttime"),
        f"company {company_id} form_start"
    )
    deadline = _build_datetime(
        item.get("regEnddate"),
        item.get("regEndtime"),
        f"company {company_id} deadline"
    )

    return Company(
        id=company_id,
        company=str(item.get("company", "")),
        company_code=str(item.get("company_code", "")),
        placementtype=str(item.get("placementtype", "")),
        maxPackage=str(item.get("maxPackage", "")),
        minPackage=str(item.get("minPackage", "")),
        tpoprogram=str(item.get("tpoprogram", "")),
        academicyear=str(item.get("academicyear", "")),
        isactive=True,  # we already filtered inactive ones
        form_start=form_start,
        deadline=deadline,
    )


def _build_datetime(
    date_str: Optional[str],
    time_str: Optional[str],
    context: str,
) -> Optional[datetime]:
    """
    Combine a date string and time string into a timezone-aware datetime.

    Both date_str and time_str can be None, empty, or whitespace.
    If either is missing, the result is None (caller should notify
    when values later get filled in).

    Format: date "%d-%b-%Y" (e.g. "25-Oct-2026"), time "%H:%M" (e.g. "09:00").
    Timezone: Asia/Kolkata (IST).
    """
    # Treat empty/whitespace strings as None
    if not date_str or not date_str.strip():
        return None
    if not time_str or not time_str.strip():
        return None

    date_str = date_str.strip()
    time_str = time_str.strip()

    try:
        combined = f"{date_str} {time_str}"
        naive = datetime.strptime(combined, config.DATETIME_FORMAT)
        # Attach IST timezone
        return naive.replace(tzinfo=config.IST)
    except ValueError as e:
        logger.warning("Failed to parse datetime for %s: '%s' '%s' – %s",
                       context, date_str, time_str, e)
        return None


def filter_by_program(companies: list[Company]) -> list[Company]:
    """
    Filter companies to only those whose tpoprogram contains any of the
    configured program filter strings.

    If filtering is disabled (PROGRAM_FILTERS is empty), returns all companies.
    """
    if not config.FILTER_ENABLED:
        return companies

    result = []
    for c in companies:
        programs = c.tpoprogram
        if any(f in programs for f in config.PROGRAM_FILTERS):
            result.append(c)
        else:
            logger.debug("Filtered out company id=%d (%s): programs '%s' "
                         "don't match filters %s",
                         c.id, c.company, programs, config.PROGRAM_FILTERS)
    logger.info("Program filter: %d/%d companies match", len(result), len(companies))
    return result
