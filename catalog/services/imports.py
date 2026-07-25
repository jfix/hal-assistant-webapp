from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from catalog.integrations.hal_assistant import read_review_snapshot, readiness_for
from catalog.models import AuditEvent, FieldAssertion, Publication, SourceImport, SourceRecord

PARSER_VERSION = f"hal-assistant/{version('hal-assistant')}"

MATERIALIZED_FIELDS = (
    "publication_type",
    "hal_document_type",
    "title",
    "publication_year",
    "language",
    "pages",
    "authors",
    "editors",
    "journal_title",
    "book_title",
    "publisher",
    "publisher_city",
    "volume",
    "issue",
    "doi",
    "isbn",
    "issn",
    "conference_title",
    "conference_start_date",
    "conference_end_date",
    "conference_city",
    "conference_country",
    "source_url",
    "review_state",
    "readiness_state",
    "missing_required_fields",
    "hal_status",
    "hal_id",
)


@dataclass(frozen=True)
class ImportEntry:
    locator: str
    publication_key: str
    title: str
    status: str
    changes: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImportPlan:
    source_name: str
    source_sha256: str
    parser_version: str
    row_count: int
    entries: tuple[ImportEntry, ...]
    errors: tuple[str, ...]
    duplicate_snapshot: bool
    report_sha256: str

    @property
    def can_apply(self) -> bool:
        return not self.errors and not any(entry.errors for entry in self.entries)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "format": "hal-webapp-import-plan-v1",
            "source_name": self.source_name,
            "source_sha256": self.source_sha256,
            "parser_version": self.parser_version,
            "row_count": self.row_count,
            "duplicate_snapshot": self.duplicate_snapshot,
            "can_apply": self.can_apply,
            "summary": {
                status: sum(entry.status == status for entry in self.entries)
                for status in ("created", "changed", "unchanged", "conflicting")
            },
            "errors": list(self.errors),
            "entries": [asdict(entry) for entry in self.entries],
            "report_sha256": self.report_sha256,
        }
        return payload


@dataclass(frozen=True)
class ImportResult:
    source_import_id: str
    report_sha256: str
    created: int
    proposed: int
    unchanged: int
    duplicate_snapshot: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _first(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value not in (None, "", []):
            return value
    return None


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _integer(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _list(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list | tuple):
        return [_text(item) for item in value if _text(item)]
    return [
        item.strip()
        for item in str(value).replace("\n", ";").split(";")
        if item.strip()
    ]


def _date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _review_state(row: dict[str, Any]) -> str:
    value = _text(_first(row, "review_state", "decision")).lower()
    if value in {"approve", "approved", "accepted", "already_on_hal"}:
        return Publication.ReviewState.APPROVED
    if value in {"blocked", "reject", "rejected"}:
        return Publication.ReviewState.BLOCKED
    if value in {"needs_review", "review", "defer"}:
        return Publication.ReviewState.NEEDS_REVIEW
    return Publication.ReviewState.DRAFT


def _readiness_state(row: dict[str, Any], ready: bool) -> str:
    imported = _text(_first(row, "readiness_state", "hal_readiness", "readiness")).lower()
    valid = {choice for choice, _ in Publication.ReadinessState.choices}
    if imported in valid:
        return imported
    return (
        Publication.ReadinessState.HAL_READY
        if ready
        else Publication.ReadinessState.NEEDS_ENRICHMENT
    )


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    publication_key = _text(_first(row, "publication_id", "publication_key"))
    publication_type = _text(row.get("publication_type"))
    authors = _list(row.get("authors"))
    year = _integer(_first(row, "year", "producedDateY"))
    normalized_for_audit = {
        **row,
        "publication_id": publication_key,
        "publication_type": publication_type,
        "document_type": _text(_first(row, "document_type", "docType")),
        "title": _text(row.get("title")),
        "year": year,
        "authors": authors,
        "language": _text(row.get("language")),
        "journal_title": _text(_first(row, "journal_title", "journal")),
        "book_title": _text(row.get("book_title")),
        "conference_start_date": _date(
            _first(row, "conference_start_date", "conferenceStartDate")
        ),
        "conference_end_date": _date(
            _first(row, "conference_end_date", "conferenceEndDate")
        ),
        "conference_title": _text(
            _first(row, "conference_title", "conferenceTitle")
        ),
        "conference_city": _text(_first(row, "conference_city", "city")),
        "conference_country": _text(_first(row, "conference_country", "country")),
    }
    ready, missing, document_type = readiness_for(normalized_for_audit)

    hal_id = _text(
        _first(row, "production_hal_id", "hal_id", "halId", "hal_match_id")
    )
    hal_status = _text(
        _first(row, "production_status", "hal_status", "hal_match_status")
    ).lower()
    if hal_id and not hal_status:
        hal_status = "existing"

    return {
        "publication_key": publication_key,
        "publication_type": publication_type,
        "hal_document_type": document_type if document_type != "UNKNOWN" else "",
        "title": _text(row.get("title")),
        "publication_year": year,
        "language": _text(row.get("language")),
        "pages": _text(row.get("pages")),
        "authors": authors,
        "editors": _list(row.get("editors")),
        "journal_title": _text(_first(row, "journal_title", "journal")),
        "book_title": _text(row.get("book_title")),
        "publisher": _text(row.get("publisher")),
        "publisher_city": _text(row.get("publisher_city")),
        "volume": _text(row.get("volume")),
        "issue": _text(row.get("issue")),
        "doi": _text(row.get("doi")),
        "isbn": _list(row.get("isbn")),
        "issn": _list(row.get("issn")),
        "conference_title": normalized_for_audit["conference_title"],
        "conference_start_date": normalized_for_audit["conference_start_date"],
        "conference_end_date": normalized_for_audit["conference_end_date"],
        "conference_city": normalized_for_audit["conference_city"],
        "conference_country": normalized_for_audit["conference_country"],
        "source_url": _text(_first(row, "source_url", "url")),
        "review_state": _review_state(row),
        "readiness_state": _readiness_state(row, ready),
        "missing_required_fields": list(missing),
        "hal_status": hal_status,
        "hal_id": hal_id,
    }


def _entry_errors(values: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not values["publication_key"]:
        errors.append("Missing publication_id")
    if not values["publication_type"]:
        errors.append("Missing publication_type")
    if not values["title"]:
        errors.append("Missing title")
    if values["publication_year"] is None:
        errors.append("Missing or invalid publication year")
    if not values["authors"]:
        errors.append("Missing authors")
    return errors


def _diff(publication: Publication, incoming: dict[str, Any]) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    for field_name in MATERIALIZED_FIELDS:
        current = _json_value(getattr(publication, field_name))
        proposed = _json_value(incoming[field_name])
        if current != proposed:
            changes[field_name] = {"current": current, "proposed": proposed}
    return changes


def _report_digest(payload: dict[str, Any]) -> str:
    return _sha256_bytes(_canonical_json(payload).encode("utf-8"))


def plan_snapshot_import(path: str | Path) -> tuple[ImportPlan, tuple[dict[str, Any], ...]]:
    source = Path(path)
    source_bytes = source.read_bytes()
    source_sha256 = _sha256_bytes(source_bytes)
    rows = tuple(_json_value(row) for row in read_review_snapshot(source))

    errors: list[str] = []
    entries: list[ImportEntry] = []
    seen_keys: set[str] = set()
    seen_identifiers: dict[tuple[str, str], str] = {}
    normalized_rows: list[dict[str, Any]] = []

    for index, row in enumerate(rows, start=2):
        values = normalize_row(row)
        normalized_rows.append(values)
        key = values["publication_key"]
        row_errors = _entry_errors(values)
        if key and key in seen_keys:
            row_errors.append(f"Duplicate publication_id in snapshot: {key}")
        seen_keys.add(key)

        for field_name in ("doi", "hal_id"):
            identifier = _text(values[field_name]).casefold()
            if not identifier:
                continue
            identity = (field_name, identifier)
            if prior_key := seen_identifiers.get(identity):
                if prior_key != key:
                    row_errors.append(
                        f"Duplicate {field_name} shared with {prior_key}: {values[field_name]}"
                    )
            else:
                seen_identifiers[identity] = key

        publication = Publication.objects.filter(publication_key=key).first() if key else None
        changes: dict[str, dict[str, Any]] = {}
        status = "created"
        if publication:
            changes = _diff(publication, values)
            status = "changed" if changes else "unchanged"
            for identity_field in ("doi", "hal_id"):
                current = _text(getattr(publication, identity_field)).casefold()
                proposed = _text(values[identity_field]).casefold()
                if current and proposed and current != proposed:
                    row_errors.append(
                        f"Conflicting {identity_field}: existing value differs"
                    )
                    status = "conflicting"

        if key:
            for identity_field in ("doi", "hal_id"):
                identifier = _text(values[identity_field])
                if not identifier:
                    continue
                duplicate = (
                    Publication.objects.filter(
                        **{f"{identity_field}__iexact": identifier}
                    )
                    .exclude(publication_key=key)
                    .first()
                )
                if duplicate:
                    row_errors.append(
                        f"{identity_field} already belongs to {duplicate.publication_key}"
                    )
                    status = "conflicting"

        if row_errors:
            status = "conflicting"
        entries.append(
            ImportEntry(
                locator=f"Publications!row-{index}",
                publication_key=key,
                title=values["title"],
                status=status,
                changes=changes,
                errors=tuple(row_errors),
            )
        )

    duplicate_snapshot = SourceImport.objects.filter(file_sha256=source_sha256).exists()
    unsigned_report = {
        "format": "hal-webapp-import-plan-v1",
        "source_sha256": source_sha256,
        "parser_version": PARSER_VERSION,
        "row_count": len(rows),
        "duplicate_snapshot": duplicate_snapshot,
        "errors": errors,
        "entries": [asdict(entry) for entry in entries],
    }
    report_sha256 = _report_digest(unsigned_report)
    plan = ImportPlan(
        source_name=source.name,
        source_sha256=source_sha256,
        parser_version=PARSER_VERSION,
        row_count=len(rows),
        entries=tuple(entries),
        errors=tuple(errors),
        duplicate_snapshot=duplicate_snapshot,
        report_sha256=report_sha256,
    )
    return plan, rows


def _assertions_for(
    publication: Publication,
    source_record: SourceRecord,
    values: dict[str, Any],
    *,
    state: str,
    fields: tuple[str, ...] | list[str],
) -> list[FieldAssertion]:
    return [
        FieldAssertion(
            publication=publication,
            source_record=source_record,
            field_path=field_name,
            value=_json_value(values[field_name]),
            normalized_value=_canonical_json(values[field_name]),
            origin="reviewed_workbook",
            confidence="human_reviewed",
            state=state,
        )
        for field_name in fields
        if values[field_name] not in (None, "", [])
    ]


def apply_snapshot_import(
    path: str | Path,
    *,
    expected_report_sha256: str,
) -> ImportResult:
    source = Path(path)
    plan, rows = plan_snapshot_import(source)
    if plan.report_sha256 != expected_report_sha256:
        raise ValueError(
            "Import report checksum changed; rerun --dry-run and review the new report"
        )
    if not plan.can_apply:
        raise ValueError("Import plan contains blocking errors")

    existing = SourceImport.objects.filter(file_sha256=plan.source_sha256).first()
    if existing:
        return ImportResult(
            source_import_id=str(existing.id),
            report_sha256=plan.report_sha256,
            created=0,
            proposed=0,
            unchanged=plan.row_count,
            duplicate_snapshot=True,
        )

    storage_name = f"snapshots/{plan.source_sha256[:2]}/{plan.source_sha256}.xlsx"
    saved_new_object = False
    if not default_storage.exists(storage_name):
        stored_file = default_storage.save(storage_name, ContentFile(source.read_bytes()))
        saved_new_object = True
    else:
        stored_file = storage_name

    created = proposed = unchanged = 0
    try:
        with transaction.atomic():
            source_import = SourceImport.objects.create(
                source_type=SourceImport.SourceType.XLSX,
                source_name=source.name,
                stored_file=stored_file,
                file_sha256=plan.source_sha256,
                parser_version=plan.parser_version,
                report_sha256=plan.report_sha256,
                record_count=plan.row_count,
                report=plan.as_dict(),
                retrieved_at=timezone.now(),
            )

            for entry, row in zip(plan.entries, rows, strict=True):
                values = normalize_row(row)
                original_citation = _first(
                    row,
                    "original_citation",
                    "raw_citation",
                )
                publication = Publication.objects.filter(
                    publication_key=values["publication_key"]
                ).first()
                if publication is None:
                    publication = Publication.objects.create(**values)
                    created += 1
                elif entry.status == "changed":
                    proposed += 1
                else:
                    unchanged += 1

                source_record = SourceRecord.objects.create(
                    source_import=source_import,
                    publication=publication,
                    locator=entry.locator,
                    original_citation=(
                        "" if original_citation is None else str(original_citation)
                    ),
                    raw_data=row,
                    record_sha256=_report_digest(row),
                )

                if entry.status == "created":
                    assertions = _assertions_for(
                        publication,
                        source_record,
                        values,
                        state=FieldAssertion.State.ACCEPTED,
                        fields=list(MATERIALIZED_FIELDS),
                    )
                elif entry.status == "changed":
                    assertions = _assertions_for(
                        publication,
                        source_record,
                        values,
                        state=FieldAssertion.State.PROPOSED,
                        fields=list(entry.changes),
                    )
                else:
                    assertions = []
                FieldAssertion.objects.bulk_create(assertions)

            AuditEvent.objects.create(
                action="source_import.applied",
                object_type="source_import",
                object_id=str(source_import.id),
                after_checksum=plan.source_sha256,
                metadata={
                    "report_sha256": plan.report_sha256,
                    "created": created,
                    "proposed": proposed,
                    "unchanged": unchanged,
                },
            )
    except Exception:
        if saved_new_object:
            default_storage.delete(stored_file)
        raise

    return ImportResult(
        source_import_id=str(source_import.id),
        report_sha256=plan.report_sha256,
        created=created,
        proposed=proposed,
        unchanged=unchanged,
        duplicate_snapshot=False,
    )
