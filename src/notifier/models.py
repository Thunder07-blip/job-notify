"""
models.py – Data classes for the notification system.

Company is the canonical representation of a portal company listing.
Uses dataclasses for clarity and immutability where possible.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Optional


@dataclasses.dataclass
class Company:
    """
    Canonical representation of a company listing from the placement portal.

    Fields map 1:1 to the spec's "keep" list. Contact fields are intentionally
    excluded for privacy. form_start/deadline are computed from regStart*/regEnd*.
    """
    id: int                              # portal id – the UNIQUE KEY
    company: str                         # company name (may be duplicated across IDs)
    company_code: str
    placementtype: str                   # "Full Time", "Internship", etc.
    maxPackage: str                      # display text, may be messy
    minPackage: str                      # display text, may be messy
    tpoprogram: str                      # comma-separated program list
    academicyear: str
    isactive: bool
    form_start: Optional[datetime] = None   # computed: regStartdate + regStarttime (IST)
    deadline: Optional[datetime] = None     # computed: regEnddate + regEndtime (IST)

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dictionary for state storage."""
        return {
            "id": self.id,
            "company": self.company,
            "company_code": self.company_code,
            "placementtype": self.placementtype,
            "maxPackage": self.maxPackage,
            "minPackage": self.minPackage,
            "tpoprogram": self.tpoprogram,
            "academicyear": self.academicyear,
            "isactive": self.isactive,
            "form_start": self.form_start.isoformat() if self.form_start else None,
            "deadline": self.deadline.isoformat() if self.deadline else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Company:
        """Deserialize from a state dictionary."""
        return cls(
            id=data["id"],
            company=data["company"],
            company_code=data.get("company_code", ""),
            placementtype=data.get("placementtype", ""),
            maxPackage=data.get("maxPackage", ""),
            minPackage=data.get("minPackage", ""),
            tpoprogram=data.get("tpoprogram", ""),
            academicyear=data.get("academicyear", ""),
            isactive=data.get("isactive", True),
            form_start=_parse_iso(data.get("form_start")),
            deadline=_parse_iso(data.get("deadline")),
        )

    def programs_list(self) -> list[str]:
        """Split the comma-separated tpoprogram into a clean list."""
        return [p.strip() for p in self.tpoprogram.split(",") if p.strip()]

    def package_display(self) -> str:
        """Human-readable package range string."""
        if self.minPackage and self.maxPackage:
            return f"{self.minPackage} – {self.maxPackage} LPA"
        if self.maxPackage:
            return f"Up to {self.maxPackage} LPA"
        if self.minPackage:
            return f"From {self.minPackage} LPA"
        return "Not specified"


@dataclasses.dataclass
class CompanyChange:
    """Represents a detected change between two snapshots of a company."""
    company: Company                 # the NEW version of the company
    change_type: str                 # "new", "start_changed", "deadline_changed"
    old_value: Optional[str] = None  # human-readable old value (for changes)
    new_value: Optional[str] = None  # human-readable new value (for changes)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 datetime string, returning None for empty/null."""
    if not value:
        return None
    return datetime.fromisoformat(value)
