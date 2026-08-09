from __future__ import annotations

from copy import deepcopy

import pytest

from catalog.models import AuditEvent, FieldAssertion, Publication, SourceImport
from catalog.services.imports import apply_snapshot_import, plan_snapshot_import

pytestmark = pytest.mark.django_db


def test_dry_run_is_mutation_free(workbook_factory, reviewed_row) -> None:
    snapshot = workbook_factory([reviewed_row])

    plan, rows = plan_snapshot_import(snapshot)

    assert plan.can_apply
    assert plan.entries[0].status == "created"
    assert len(rows) == 1
    assert Publication.objects.count() == 0
    assert SourceImport.objects.count() == 0
    assert AuditEvent.objects.count() == 0


def test_apply_requires_the_reviewed_report_checksum(
    workbook_factory,
    reviewed_row,
) -> None:
    snapshot = workbook_factory([reviewed_row])

    with pytest.raises(ValueError, match="checksum changed"):
        apply_snapshot_import(snapshot, expected_report_sha256="0" * 64)

    assert Publication.objects.count() == 0


def test_apply_materializes_new_record_and_preserves_evidence(
    workbook_factory,
    reviewed_row,
) -> None:
    snapshot = workbook_factory([reviewed_row])
    plan, _ = plan_snapshot_import(snapshot)

    result = apply_snapshot_import(
        snapshot,
        expected_report_sha256=plan.report_sha256,
    )

    publication = Publication.objects.get(publication_key="pub-0001")
    source_import = SourceImport.objects.get()
    source_record = source_import.records.get()
    assert result.created == 1
    assert publication.title == reviewed_row["title"]
    assert publication.authors == ["Ada Lovelace", "Grace Hopper"]
    assert source_record.original_citation == reviewed_row["original_citation"]
    assert source_record.raw_data["original_citation"] == reviewed_row["original_citation"]
    assert source_record.assertions.filter(
        state=FieldAssertion.State.ACCEPTED
    ).exists()
    assert AuditEvent.objects.filter(action="source_import.applied").count() == 1
    assert source_import.stored_file.endswith(f"{plan.source_sha256}.xlsx")


def test_imported_hal_record_starts_with_a_synchronization_baseline(
    workbook_factory,
    reviewed_row,
) -> None:
    hal_row = {**reviewed_row, "hal_id": "hal-01234567"}
    snapshot = workbook_factory([hal_row])
    plan, _ = plan_snapshot_import(snapshot)

    apply_snapshot_import(snapshot, expected_report_sha256=plan.report_sha256)

    publication = Publication.objects.get(publication_key="pub-0001")
    assert publication.hal_id == "hal-01234567"
    assert publication.hal_synced_version == publication.version
    assert publication.workflow_statuses[-1]["label"] == "À jour"


def test_reapplying_identical_bytes_is_an_idempotent_no_op(
    workbook_factory,
    reviewed_row,
) -> None:
    snapshot = workbook_factory([reviewed_row])
    first_plan, _ = plan_snapshot_import(snapshot)
    apply_snapshot_import(
        snapshot,
        expected_report_sha256=first_plan.report_sha256,
    )

    second_plan, _ = plan_snapshot_import(snapshot)
    result = apply_snapshot_import(
        snapshot,
        expected_report_sha256=second_plan.report_sha256,
    )

    assert second_plan.duplicate_snapshot
    assert result.duplicate_snapshot
    assert result.unchanged == 1
    assert Publication.objects.count() == 1
    assert SourceImport.objects.count() == 1
    assert AuditEvent.objects.count() == 1


def test_changed_snapshot_creates_proposals_without_mutating_publication(
    workbook_factory,
    reviewed_row,
) -> None:
    first = workbook_factory([reviewed_row], name="first.xlsx")
    first_plan, _ = plan_snapshot_import(first)
    apply_snapshot_import(first, expected_report_sha256=first_plan.report_sha256)

    changed_row = deepcopy(reviewed_row)
    changed_row["title"] = "A proposed replacement title"
    second = workbook_factory([changed_row], name="second.xlsx")
    second_plan, _ = plan_snapshot_import(second)
    result = apply_snapshot_import(
        second,
        expected_report_sha256=second_plan.report_sha256,
    )

    publication = Publication.objects.get(publication_key="pub-0001")
    proposed = publication.assertions.get(
        field_path="title",
        state=FieldAssertion.State.PROPOSED,
    )
    assert result.proposed == 1
    assert publication.title == reviewed_row["title"]
    assert proposed.value == "A proposed replacement title"
    assert SourceImport.objects.count() == 2


def test_duplicate_identifiers_block_apply(workbook_factory, reviewed_row) -> None:
    duplicate = deepcopy(reviewed_row)
    duplicate["publication_id"] = "pub-0002"
    duplicate["title"] = "Another title"
    snapshot = workbook_factory([reviewed_row, duplicate])

    plan, _ = plan_snapshot_import(snapshot)

    assert not plan.can_apply
    assert plan.entries[1].status == "conflicting"
    assert any("Duplicate doi" in error for error in plan.entries[1].errors)
    with pytest.raises(ValueError, match="blocking errors"):
        apply_snapshot_import(
            snapshot,
            expected_report_sha256=plan.report_sha256,
        )
