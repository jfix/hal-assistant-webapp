from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.utils.translation import gettext_lazy as _
from hal_assistant.hal_requirements import audit_record
from hal_assistant.hal_xml import HAL_NS, TEI_NS, XSI_NS, build_tei, validate_tei
from hal_assistant.review_cli import HAL_DOCUMENT_TYPES
from hal_assistant.review_import import read_publications_sheet

if TYPE_CHECKING:
    from catalog.models import Publication

# Fallback domain when a review row does not carry its own ``hal_domain``.
DEFAULT_HAL_DOMAIN = "shs.litt"

HAL_DOCUMENT_TYPE_LABELS_FR = {
    "ART": _("Article dans une revue"),
    "COMM": _("Communication dans un congrès"),
    "COUV": _("Chapitre d’ouvrage"),
    "OUV": _("Ouvrages scientifiques (y compris édition critique et traduction)"),
    "DOUV": _("Direction d’ouvrage"),
}


def hal_document_type_display(
    *, publication_type: str, explicit_type: str = ""
) -> tuple[str, str]:
    """Return the HAL code and its translated interface label.

    Imported HAL evidence wins. New local drafts fall back to the mapping owned
    by the pinned reusable package rather than duplicating classification rules.
    """
    code = explicit_type.strip().upper() or HAL_DOCUMENT_TYPES.get(
        publication_type, publication_type.upper()
    )
    label = HAL_DOCUMENT_TYPE_LABELS_FR.get(
        code, _("Type de document HAL : %(code)s") % {"code": code}
    )
    return code, label


def publication_types_for_hal(code: str) -> tuple[str, ...]:
    """Return package publication types represented by one HAL document code."""
    wanted = code.strip().upper()
    return tuple(
        publication_type
        for publication_type, hal_code in HAL_DOCUMENT_TYPES.items()
        if hal_code == wanted
    )


def read_review_snapshot(path: str | Path) -> list[dict[str, Any]]:
    """Read the reviewed Publications worksheet through the reusable package."""
    return read_publications_sheet(path)


def readiness_for(record: dict[str, Any]) -> tuple[bool, list[str], str]:
    """Return the package-owned HAL readiness decision for one normalized row."""
    result = audit_record(record)
    return result.ready, result.missing_required_fields, result.document_type


def readiness_for_publication(publication: Publication) -> tuple[bool, list[str], str]:
    """Re-audit the current materialized metadata against HAL requirements."""
    source = publication.source_records.order_by("-created_at").first()
    original = source.raw_data if source is not None else {}
    return readiness_for(_current_record(original, publication))


@dataclass(frozen=True)
class SubmissionXML:
    """A notice-only AOfr TEI preview for one review record."""

    xml: str
    errors: list[str] = field(default_factory=list)
    domain: str = ""
    idhal: str = ""
    structure_id: str = ""


def _current_record(record: dict[str, Any], publication: Publication) -> dict[str, Any]:
    """Overlay reviewed materialized fields without modifying source evidence."""
    current = dict(record)
    values = {
        "title": publication.title,
        "document_type": publication.hal_document_type,
        "year": publication.publication_year,
        "language": publication.language,
        "abstract_en": publication.abstract_en,
        "abstract_fr": publication.abstract_fr,
        "keywords_en": publication.keywords_en,
        "keywords_fr": publication.keywords_fr,
        "authors": publication.authors,
        "editors": publication.editors,
        "container_title": publication.journal_title or publication.book_title,
        "publisher": publication.publisher,
        "publisher_city": publication.publisher_city,
        "volume": publication.volume,
        "issue": publication.issue,
        "pages": publication.pages,
        "doi": publication.doi,
        "isbn": publication.isbn,
        "issn": publication.issn,
        "conference_title": publication.conference_title,
        "conference_start_date": publication.conference_start_date,
        "conference_end_date": publication.conference_end_date,
        "conference_city": publication.conference_city,
        "conference_country": publication.conference_country,
        "source_url": publication.source_url,
    }
    current.update(values)
    return current


def build_submission_xml(
    record: dict[str, Any], *, publication: Publication | None = None
) -> SubmissionXML:
    """Generate the HAL AOfr TEI submission notice for one review record.

    This is a read-only debugging preview built entirely by the pinned package.
    It never contacts HAL. Mandatory-metadata gaps and local schema-validation
    findings are returned as ``errors`` rather than raised, so the debug view can
    display exactly why a record would be rejected.
    """
    if publication is not None:
        record = _current_record(record, publication)
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
