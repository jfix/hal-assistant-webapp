from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hal_assistant.hal_requirements import audit_record
from hal_assistant.hal_xml import HAL_NS, TEI_NS, XSI_NS, build_tei, validate_tei
from hal_assistant.review_import import read_publications_sheet

# Fallback domain when a review row does not carry its own ``hal_domain``.
DEFAULT_HAL_DOMAIN = "shs.litt"


def read_review_snapshot(path: str | Path) -> list[dict[str, Any]]:
    """Read the reviewed Publications worksheet through the reusable package."""
    return read_publications_sheet(path)


def readiness_for(record: dict[str, Any]) -> tuple[bool, list[str], str]:
    """Return the package-owned HAL readiness decision for one normalized row."""
    result = audit_record(record)
    return result.ready, result.missing_required_fields, result.document_type


@dataclass(frozen=True)
class SubmissionXML:
    """A notice-only AOfr TEI preview for one review record."""

    xml: str
    errors: list[str] = field(default_factory=list)
    domain: str = ""
    idhal: str = ""
    structure_id: str = ""


def build_submission_xml(record: dict[str, Any]) -> SubmissionXML:
    """Generate the HAL AOfr TEI submission notice for one review record.

    This is a read-only debugging preview built entirely by the pinned package.
    It never contacts HAL. Mandatory-metadata gaps and local schema-validation
    findings are returned as ``errors`` rather than raised, so the debug view can
    display exactly why a record would be rejected.
    """
    domain = str(record.get("hal_domain") or record.get("domain") or DEFAULT_HAL_DOMAIN)
    idhal = record.get("idhal") or None
    structure_id = str(
        record.get("structure_id") or record.get("hal_structure_id") or ""
    )
    try:
        tree = build_tei(record, domain=domain, idhal=idhal)
    except (TypeError, ValueError) as exc:
        return SubmissionXML(
            xml="",
            errors=[str(exc)],
            domain=domain,
            idhal=str(idhal or ""),
            structure_id=structure_id,
        )
    return SubmissionXML(
        xml=_serialize(tree),
        errors=validate_tei(tree),
        domain=domain,
        idhal=str(idhal or ""),
        structure_id=structure_id,
    )


def _serialize(tree: ET.ElementTree) -> str:
    # Mirror the package's write boundary so the previewed XML matches what a
    # submission would carry, without emitting ns0-prefixed TEI.
    ET.register_namespace("", TEI_NS)
    ET.register_namespace("hal", HAL_NS)
    ET.register_namespace("xsi", XSI_NS)
    ET.indent(tree, space="  ")
    return ET.tostring(tree.getroot(), encoding="utf-8", xml_declaration=True).decode("utf-8")
