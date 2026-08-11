from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from catalog.models import (
    AssertionDecision,
    AuditEvent,
    FieldAssertion,
    Publication,
    SourceImport,
    SourceRecord,
)
from catalog.services.review import (
    ReviewConflict,
    ReviewError,
    decide_assertion,
    edit_field,
    pending_proposals,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def proposal() -> FieldAssertion:
    publication = Publication.objects.create(
        publication_key="pub-review-1",
        publication_type="journal_article",
        title="Original title",
        publication_year=2023,
        authors=["Ada Lovelace"],
        version=1,
    )
    source_import = SourceImport.objects.create(
        source_type=SourceImport.SourceType.XLSX,
        source_name="changeset.xlsx",
        stored_file="snapshots/aa/aaaa.xlsx",
        file_sha256="a" * 64,
        parser_version="hal-assistant/test",
        report_sha256="b" * 64,
        record_count=1,
        report={},
        retrieved_at=timezone.now(),
    )
    source_record = SourceRecord.objects.create(
        source_import=source_import,
        publication=publication,
        locator="Publications!row-2",
        raw_data={"title": "Revised title"},
        record_sha256="c" * 64,
    )
    return FieldAssertion.objects.create(
        publication=publication,
        source_record=source_record,
        field_path="title",
        value="Revised title",
        origin="reviewed_workbook",
        confidence="human_reviewed",
        state=FieldAssertion.State.PROPOSED,
    )


def test_accept_materializes_value_and_bumps_version(proposal) -> None:
    decision = decide_assertion(
        assertion=proposal,
        actor=None,
        outcome=AssertionDecision.Outcome.ACCEPTED,
        base_version=1,
    )

    publication = Publication.objects.get(pk=proposal.publication_id)
    assert publication.title == "Revised title"
    assert publication.version == 2
    assert decision.resulting_version == 2
    assert not pending_proposals(publication)
    assert AuditEvent.objects.filter(action="assertion.accepted").count() == 1


@pytest.mark.parametrize(
    ("field_name", "raw_value", "expected"),
    [
        ("abstract_fr", "Résumé révisé et développé.", "Résumé révisé et développé."),
        ("keywords_en", "archive; memory, theatre", ["archive", "memory, theatre"]),
    ],
)
def test_abstracts_and_keywords_are_audited_editable_fields(
    proposal, field_name, raw_value, expected
) -> None:
    publication = proposal.publication

    decision = edit_field(
        publication=publication,
        field_path=field_name,
        actor=None,
        edited_value=raw_value,
        base_version=1,
    )

    publication.refresh_from_db()
    assert getattr(publication, field_name) == expected
    assert publication.version == 2
    assert decision.field_path == field_name
    assert decision.applied_value == expected


def test_metadata_edit_recomputes_hal_readiness_and_invalidates_preprod() -> None:
    publication = Publication.objects.create(
        publication_key="readiness-after-edit",
        publication_type="journal_article",
        hal_document_type="ART",
        title="A complete article",
        publication_year=2024,
        language="en",
        authors=["Ada Lovelace"],
        journal_title="Journal of Complete Metadata",
        readiness_state=Publication.ReadinessState.PREPROD_VALIDATED,
    )
    source_import = SourceImport.objects.create(
        source_type=SourceImport.SourceType.XLSX,
        source_name="readiness.xlsx",
        stored_file="snapshots/readiness.xlsx",
        file_sha256="d" * 64,
        parser_version="hal-assistant/test",
        report_sha256="e" * 64,
        record_count=1,
        report={},
        retrieved_at=timezone.now(),
    )
    SourceRecord.objects.create(
        source_import=source_import,
        publication=publication,
        locator="Publications!2",
        raw_data={
            "title": publication.title,
            "document_type": "ART",
            "year": 2024,
            "language": "en",
            "authors": "Ada Lovelace",
            "container_title": "Journal of Complete Metadata",
            "hal_domain": "shs.litt",
            "idhal": "florence-fix",
        },
        record_sha256="f" * 64,
    )

    edit_field(
        publication=publication,
        field_path="title",
        actor=None,
        edited_value="A still complete article",
        base_version=1,
    )
    publication.refresh_from_db()
    assert publication.readiness_state == Publication.ReadinessState.HAL_READY
    assert publication.missing_required_fields == []

    edit_field(
        publication=publication,
        field_path="authors",
        actor=None,
        edited_value="",
        base_version=2,
    )
    publication.refresh_from_db()
    assert publication.readiness_state == Publication.ReadinessState.NEEDS_ENRICHMENT
    assert publication.missing_required_fields


def test_reject_keeps_value_and_records_decision(proposal) -> None:
    decision = decide_assertion(
        assertion=proposal,
        actor=None,
        outcome=AssertionDecision.Outcome.REJECTED,
        base_version=1,
        reason="Container-level value, not this record",
    )

    publication = Publication.objects.get(pk=proposal.publication_id)
    assert publication.title == "Original title"
    assert publication.version == 1
    assert decision.outcome == AssertionDecision.Outcome.REJECTED
    assert not pending_proposals(publication)
    assert AuditEvent.objects.filter(action="assertion.rejected").count() == 1


def test_stale_version_raises_conflict_and_changes_nothing(proposal) -> None:
    with pytest.raises(ReviewConflict):
        decide_assertion(
            assertion=proposal,
            actor=None,
            outcome=AssertionDecision.Outcome.ACCEPTED,
            base_version=0,
        )

    publication = Publication.objects.get(pk=proposal.publication_id)
    assert publication.title == "Original title"
    assert publication.version == 1
    assert AssertionDecision.objects.count() == 0


def test_a_proposal_cannot_be_decided_twice(proposal) -> None:
    decide_assertion(
        assertion=proposal,
        actor=None,
        outcome=AssertionDecision.Outcome.ACCEPTED,
        base_version=1,
    )
    with pytest.raises(ReviewError):
        decide_assertion(
            assertion=proposal,
            actor=None,
            outcome=AssertionDecision.Outcome.REJECTED,
            base_version=2,
        )


def test_edit_materializes_a_reviewer_supplied_value(proposal) -> None:
    decision = decide_assertion(
        assertion=proposal,
        actor=None,
        outcome=AssertionDecision.Outcome.EDITED,
        base_version=1,
        edited_value="A reviewer's own title",
        reason="Neither current nor proposed was right",
    )

    publication = Publication.objects.get(pk=proposal.publication_id)
    assert publication.title == "A reviewer's own title"
    assert publication.version == 2
    assert decision.outcome == AssertionDecision.Outcome.EDITED
    assert decision.applied_value == "A reviewer's own title"
    assert not pending_proposals(publication)
    assert AuditEvent.objects.filter(action="assertion.edited").count() == 1


def test_edit_coerces_list_fields(proposal) -> None:
    # Re-point the proposal at a list-typed field to exercise coercion.
    publication = Publication.objects.get(pk=proposal.publication_id)
    list_proposal = FieldAssertion.objects.create(
        publication=publication,
        source_record=proposal.source_record,
        field_path="authors",
        value=["Someone Else"],
        origin="reviewed_workbook",
        state=FieldAssertion.State.PROPOSED,
    )

    decide_assertion(
        assertion=list_proposal,
        actor=None,
        outcome=AssertionDecision.Outcome.EDITED,
        base_version=1,
        edited_value="Ada Lovelace; Grace Hopper",
    )

    publication.refresh_from_db()
    assert publication.authors == ["Ada Lovelace", "Grace Hopper"]


def test_edit_without_a_value_is_rejected(proposal) -> None:
    with pytest.raises(ReviewError):
        decide_assertion(
            assertion=proposal,
            actor=None,
            outcome=AssertionDecision.Outcome.EDITED,
            base_version=1,
            edited_value=None,
        )


def test_reviewer_can_edit_through_the_view(client, proposal) -> None:
    reviewer = get_user_model().objects.create_user(username="rev2", password="pw")
    reviewer.user_permissions.add(
        Permission.objects.get(codename="review_publication")
    )
    client.force_login(reviewer)

    response = client.post(
        reverse("assertion-decide", args=[proposal.publication_id, proposal.id]),
        {"outcome": "edited", "base_version": "1", "edited_value": "Edited via the view"},
    )

    assert response.status_code == 302
    publication = Publication.objects.get(pk=proposal.publication_id)
    assert publication.title == "Edited via the view"
    assert publication.version == 2


def test_edit_field_changes_value_without_a_proposal(proposal) -> None:
    publication = Publication.objects.get(pk=proposal.publication_id)

    decision = edit_field(
        publication=publication,
        field_path="publisher",
        actor=None,
        edited_value="Éditions du Test",
        base_version=1,
        reason="Correction directe",
    )

    publication.refresh_from_db()
    assert publication.publisher == "Éditions du Test"
    assert publication.version == 2
    assert decision.outcome == AssertionDecision.Outcome.EDITED
    assert decision.assertion is None
    assert decision.previous_value == ""
    assert decision.applied_value == "Éditions du Test"
    assert AuditEvent.objects.filter(action="field.edited").count() == 1


def test_edit_field_refuses_non_editable_fields(proposal) -> None:
    publication = Publication.objects.get(pk=proposal.publication_id)
    for protected in ("doi", "hal_id", "publication_type", "readiness_state"):
        with pytest.raises(ReviewError):
            edit_field(
                publication=publication,
                field_path=protected,
                actor=None,
                edited_value="x",
                base_version=1,
            )


def test_edit_field_honours_the_optimistic_version(proposal) -> None:
    publication = Publication.objects.get(pk=proposal.publication_id)
    with pytest.raises(ReviewConflict):
        edit_field(
            publication=publication,
            field_path="publisher",
            actor=None,
            edited_value="x",
            base_version=999,
        )
    publication.refresh_from_db()
    assert publication.version == 1


def test_reviewer_can_edit_a_field_through_the_view(client, proposal) -> None:
    reviewer = get_user_model().objects.create_user(username="rev3", password="pw")
    reviewer.user_permissions.add(
        Permission.objects.get(codename="review_publication")
    )
    client.force_login(reviewer)

    response = client.post(
        reverse("publication-edit-field", args=[proposal.publication_id]),
        {"field": "pages", "edited_value": "12-42", "base_version": "1"},
    )

    assert response.status_code == 302
    publication = Publication.objects.get(pk=proposal.publication_id)
    assert publication.pages == "12-42"
    assert publication.version == 2


def test_edit_field_view_requires_permission(client, proposal) -> None:
    plain = get_user_model().objects.create_user(username="plain2", password="pw")
    client.force_login(plain)

    response = client.post(
        reverse("publication-edit-field", args=[proposal.publication_id]),
        {"field": "pages", "edited_value": "999", "base_version": "1"},
    )

    assert response.status_code == 302
    assert Publication.objects.get(pk=proposal.publication_id).pages != "999"


def test_view_requires_review_permission(client, proposal) -> None:
    plain = get_user_model().objects.create_user(username="plain", password="pw")
    client.force_login(plain)

    response = client.post(
        reverse("assertion-decide", args=[proposal.publication_id, proposal.id]),
        {"outcome": "accepted", "base_version": "1"},
    )

    assert response.status_code == 302
    assert Publication.objects.get(pk=proposal.publication_id).title == "Original title"
    assert AssertionDecision.objects.count() == 0


def test_reviewer_can_accept_through_the_view(client, proposal) -> None:
    reviewer = get_user_model().objects.create_user(username="rev", password="pw")
    reviewer.user_permissions.add(
        Permission.objects.get(codename="review_publication")
    )
    client.force_login(reviewer)

    response = client.post(
        reverse("assertion-decide", args=[proposal.publication_id, proposal.id]),
        {"outcome": "accepted", "base_version": "1"},
    )

    assert response.status_code == 302
    publication = Publication.objects.get(pk=proposal.publication_id)
    assert publication.title == "Revised title"
    assert publication.version == 2
