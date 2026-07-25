from __future__ import annotations

from pathlib import Path
from typing import Any

from hal_assistant.hal_requirements import audit_record
from hal_assistant.review_import import read_publications_sheet


def read_review_snapshot(path: str | Path) -> list[dict[str, Any]]:
    """Read the reviewed Publications worksheet through the reusable package."""
    return read_publications_sheet(path)


def readiness_for(record: dict[str, Any]) -> tuple[bool, list[str], str]:
    """Return the package-owned HAL readiness decision for one normalized row."""
    result = audit_record(record)
    return result.ready, result.missing_required_fields, result.document_type
