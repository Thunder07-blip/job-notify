"""
test_parser.py – Tests for parsing portal API responses.

Covers:
- Parsing the sample JSON with all field types.
- Null/empty date+time handling.
- Duplicate company names with different IDs.
- Unusual package strings.
- Inactive company filtering.
- Program filtering.
- IST datetime conversion.
- Malformed entry handling.
"""

import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from notifier.parser import parse_response, filter_by_program, _build_datetime, ParseError
from notifier.models import Company
import notifier.config as config

IST = ZoneInfo("Asia/Kolkata")


class TestParseResponse:
    """Tests for the main parse_response function."""

    def test_parse_sample(self, sample_response):
        """Parse sample JSON and verify correct number of active companies."""
        companies = parse_response(sample_response)
        # Sample has 6 total, 1 inactive (id=1004), so 5 active
        assert len(companies) == 5

    def test_all_companies_have_ids(self, sample_response):
        """Every parsed company must have a valid integer ID."""
        companies = parse_response(sample_response)
        for c in companies:
            assert isinstance(c.id, int)
            assert c.id > 0

    def test_inactive_filtered_out(self, sample_response):
        """Companies with isactive=false are excluded."""
        companies = parse_response(sample_response)
        ids = {c.id for c in companies}
        assert 1004 not in ids  # GlobalSoft is inactive

    def test_duplicate_names_different_ids(self, sample_response):
        """Same company name can appear with different IDs."""
        companies = parse_response(sample_response)
        techcorp = [c for c in companies if c.company == "TechCorp Solutions"]
        assert len(techcorp) == 2
        assert techcorp[0].id != techcorp[1].id

    def test_null_times(self, sample_response):
        """Companies with empty date/time fields get None for form_start/deadline."""
        companies = parse_response(sample_response)
        startup = next(c for c in companies if c.id == 1005)
        assert startup.form_start is None
        assert startup.deadline is None

    def test_messy_package_strings(self, sample_response):
        """Package fields with unusual formats are stored as-is."""
        companies = parse_response(sample_response)
        innovate = next(c for c in companies if c.id == 1002)
        assert innovate.maxPackage == "7.0JS:7"

    def test_contact_fields_stripped(self, sample_response):
        """Contact fields must NOT be in parsed companies."""
        companies = parse_response(sample_response)
        for c in companies:
            assert not hasattr(c, "contact_person")
            assert not hasattr(c, "contact_email")
            assert not hasattr(c, "contact_phone")

    def test_empty_response(self):
        """Missing company_list raises ParseError."""
        with pytest.raises(ParseError):
            parse_response({})

    def test_non_dict_response(self):
        """Non-dict response raises ParseError."""
        with pytest.raises(ParseError):
            parse_response("not a dict")

    def test_non_list_company_list(self):
        """Non-list company_list raises ParseError."""
        with pytest.raises(ParseError):
            parse_response({"company_list": "not a list"})

    def test_malformed_entry_skipped(self):
        """Malformed entries are skipped, not crashed on."""
        raw = {
            "company_list": [
                {"id": 1, "company": "Good Co", "isactive": True,
                 "regStartdate": "", "regStarttime": "", "regEnddate": "", "regEndtime": ""},
                "not a dict",  # malformed
                {"no_id": True},  # missing id
            ]
        }
        companies = parse_response(raw)
        assert len(companies) == 1
        assert companies[0].company == "Good Co"


class TestBuildDatetime:
    """Tests for the _build_datetime helper."""

    def test_valid_date_time(self):
        """Correct date+time produces IST datetime."""
        dt = _build_datetime("25-Oct-2026", "09:00", "test")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 10
        assert dt.day == 25
        assert dt.hour == 9
        assert dt.minute == 0
        assert dt.tzinfo == IST

    def test_null_date(self):
        """None date returns None."""
        assert _build_datetime(None, "09:00", "test") is None

    def test_empty_date(self):
        """Empty string date returns None."""
        assert _build_datetime("", "09:00", "test") is None

    def test_null_time(self):
        """None time returns None."""
        assert _build_datetime("25-Oct-2026", None, "test") is None

    def test_empty_time(self):
        """Empty string time returns None."""
        assert _build_datetime("25-Oct-2026", "", "test") is None

    def test_whitespace_only(self):
        """Whitespace-only strings return None."""
        assert _build_datetime("  ", "09:00", "test") is None
        assert _build_datetime("25-Oct-2026", "  ", "test") is None

    def test_invalid_format(self):
        """Invalid format returns None (logged, not raised)."""
        assert _build_datetime("2026-10-25", "09:00", "test") is None  # wrong date format
        assert _build_datetime("25-Oct-2026", "9am", "test") is None   # wrong time format

    def test_midnight(self):
        """Midnight time parses correctly."""
        dt = _build_datetime("23-Oct-2026", "00:00", "test")
        assert dt.hour == 0
        assert dt.minute == 0


class TestISTimezone:
    """Tests for timezone handling."""

    def test_ist_offset(self):
        """IST is UTC+5:30."""
        dt = _build_datetime("25-Oct-2026", "09:00", "test")
        utc_dt = dt.astimezone(ZoneInfo("UTC"))
        assert utc_dt.hour == 3  # 09:00 IST = 03:30 UTC
        assert utc_dt.minute == 30

    def test_timezone_aware(self):
        """Parsed datetimes must be timezone-aware."""
        dt = _build_datetime("25-Oct-2026", "12:00", "test")
        assert dt.tzinfo is not None


class TestProgramFilter:
    """Tests for program filtering."""

    def test_filter_matches(self, sample_response, monkeypatch):
        """Companies matching the program filter are kept."""
        monkeypatch.setattr(config, "PROGRAM_FILTERS", ["VIT-BTech-Computer Engineering"])
        monkeypatch.setattr(config, "FILTER_ENABLED", True)
        companies = parse_response(sample_response)
        filtered = filter_by_program(companies)
        for c in filtered:
            assert "VIT-BTech-Computer Engineering" in c.tpoprogram

    def test_filter_excludes(self, sample_response, monkeypatch):
        """Companies NOT matching the filter are excluded."""
        monkeypatch.setattr(config, "PROGRAM_FILTERS", ["VIT-BTech-Computer Engineering"])
        monkeypatch.setattr(config, "FILTER_ENABLED", True)
        companies = parse_response(sample_response)
        filtered = filter_by_program(companies)
        # id=1003 is Mechanical only
        ids = {c.id for c in filtered}
        assert 1003 not in ids

    def test_filter_disabled(self, sample_response, monkeypatch):
        """When filtering is disabled, all companies pass."""
        monkeypatch.setattr(config, "PROGRAM_FILTERS", [])
        monkeypatch.setattr(config, "FILTER_ENABLED", False)
        companies = parse_response(sample_response)
        filtered = filter_by_program(companies)
        assert len(filtered) == len(companies)

    def test_multiple_filters(self, sample_response, monkeypatch):
        """Multiple filter strings work with OR logic."""
        monkeypatch.setattr(config, "PROGRAM_FILTERS",
                            ["VIT-BTech-Computer Engineering", "VIT-BTech-Mechanical Engineering"])
        monkeypatch.setattr(config, "FILTER_ENABLED", True)
        companies = parse_response(sample_response)
        filtered = filter_by_program(companies)
        # Should include both CS and Mechanical companies
        ids = {c.id for c in filtered}
        assert 1001 in ids  # CS
        assert 1003 in ids  # Mechanical
