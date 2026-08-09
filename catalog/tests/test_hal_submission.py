from __future__ import annotations

import hashlib
import io
import json
from unittest.mock import Mock, patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from hal_assistant.sword import SWORDResult

from catalog.models import (
    AuditEvent,
    HALOperation,
    HALPayload,
    HALSubmissionAttempt,
    Publication,
    SourceImport,
    SourceRecord,
)
from catalog.services.hal_submission import (
    HALDuplicateError,
    HALSubmissionError,
    check_live_duplicates,
    execute_preprod_operation,
    prepare_preprod_operation,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def submitter_user():
    user = get_user_model().objects.create_user(username="hal-submitter", password="pw")
    user.user_permissions.add(Permission.objects.get(codename="submit_hal_preprod"))
    return user


@pytest.fixture
def ready_publication() -> Publication:
    publication = Publication.objects.create(
        publication_key="deposit-1",
        publication_type="journal_article",
        hal_document_type="ART",
        title="Archives and memory",
        publication_year=2024,
        authors=["Ada Lovelace"],
        readiness_state=Publication.ReadinessState.HAL_READY,
    )
    source_import = SourceImport.objects.create(
        source_type=SourceImport.SourceType.XLSX,
        source_name="review.xlsx",
        stored_file="snapshots/review.xlsx",
        file_sha256="1" * 64,
        parser_version="test",
        report_sha256="2" * 64,
        record_count=1,
        report={},
        retrieved_at=timezone.now(),
    )
    SourceRecord.objects.create(
        source_import=source_import,
        publication=publication,
        locator="Publications!2",
        original_citation="Ada Lovelace. Archives and memory. 2024.",
        raw_data={
            "title": publication.title,
            "document_type": "ART",
            "year": 2024,
            "authors": "Ada Lovelace",
            "hal_domain": "shs.litt",
            "idhal": "florence-fix",
        },
        record_sha256="3" * 64,
    )
    return publication


def clear_duplicate_check(publication):
    return {"algorithm": "test", "candidates": [], "blocked": False}


def test_prepare_freezes_validated_payload_and_is_idempotent(
    ready_publication, submitter_user
) -> None:
    operation = prepare_preprod_operation(
        publication=ready_publication,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )
    repeated = prepare_preprod_operation(
        publication=ready_publication,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )

    assert repeated.id == operation.id
    assert operation.state == HALOperation.State.PREPARED
    assert operation.payload.environment == "preprod"
    assert operation.payload.validation_errors == []
    assert operation.payload.sha256 == hashlib.sha256(
        operation.payload.content.encode()
    ).hexdigest()
    assert "<TEI" in operation.payload.content
    assert AuditEvent.objects.filter(action="hal.preprod.prepared").count() == 1
    with pytest.raises(ValueError, match="immutable"):
        operation.payload.save()


def test_prepare_blocks_existing_hal_id(ready_publication, submitter_user) -> None:
    ready_publication.hal_id = "hal-123"
    ready_publication.save()

    with pytest.raises(HALSubmissionError, match="déjà"):
        prepare_preprod_operation(
            publication=ready_publication,
            actor=submitter_user,
            duplicate_checker=clear_duplicate_check,
        )

    assert not HALOperation.objects.exists()


def test_database_rejects_non_preprod_payload(ready_publication, submitter_user) -> None:
    operation = HALOperation.objects.create(
        publication=ready_publication,
        requested_by=submitter_user,
        publication_version=ready_publication.version,
        state=HALOperation.State.PREPARED,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        HALPayload.objects.create(
            operation=operation,
            environment="production",
            content="<TEI/>",
            sha256="a" * 64,
        )


def test_prepare_preserves_duplicate_block_evidence(
    ready_publication, submitter_user
) -> None:
    check = {
        "algorithm": "multifield-v1",
        "blocked": True,
        "candidates": [{"hal_id": "hal-123", "year_match": True}],
    }

    def blocked(_publication):
        raise HALDuplicateError("duplicate", check=check)

    with pytest.raises(HALDuplicateError):
        prepare_preprod_operation(
            publication=ready_publication,
            actor=submitter_user,
            duplicate_checker=blocked,
        )

    event = AuditEvent.objects.get(action="hal.preprod.duplicate_blocked")
    assert event.metadata == check
    assert not HALOperation.objects.exists()


def test_live_duplicate_check_uses_corroborating_fields(ready_publication) -> None:
    response = io.BytesIO(
        json.dumps(
            {
                "response": {
                    "docs": [
                        {
                            "halId_s": "hal-999",
                            "title_s": ready_publication.title,
                            "producedDateY_i": 2024,
                            "authFullName_s": ["Ada Lovelace"],
                            "docType_s": "ART",
                        }
                    ]
                }
            }
        ).encode()
    )

    with pytest.raises(HALDuplicateError) as caught:
        check_live_duplicates(ready_publication, opener=lambda *args, **kwargs: response)

    candidate = caught.value.check["candidates"][0]
    assert candidate["classification"] == "probable_duplicate"
    assert candidate["year_match"] is True
    assert candidate["author_match"] is True


def test_title_only_similarity_requires_review_but_is_not_a_duplicate_decision(
    ready_publication,
) -> None:
    response = io.BytesIO(
        json.dumps(
            {
                "response": {
                    "docs": [
                        {"halId_s": "hal-999", "title_s": ready_publication.title}
                    ]
                }
            }
        ).encode()
    )

    with pytest.raises(HALDuplicateError) as caught:
        check_live_duplicates(ready_publication, opener=lambda *args, **kwargs: response)

    assert caught.value.check["blocked"] is True
    assert caught.value.check["candidates"][0]["classification"] == "needs_review"


def test_execute_is_preprod_test_only_and_records_immutable_attempt(
    ready_publication, submitter_user
) -> None:
    operation = prepare_preprod_operation(
        publication=ready_publication,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )
    submitter = Mock(
        return_value=SWORDResult(
            xml_file="payload.xml",
            status_code=202,
            accepted=True,
            response_body="accepted",
            sha256=operation.payload.sha256,
        )
    )

    attempt = execute_preprod_operation(
        operation=operation,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
        submitter=submitter,
    )

    assert attempt.accepted is True
    assert attempt.environment == "preprod"
    assert attempt.test_mode is True
    assert attempt.payload_id == operation.payload.id
    assert submitter.call_args.kwargs["environment"] == "preprod"
    assert submitter.call_args.kwargs["test"] is True
    operation.refresh_from_db()
    ready_publication.refresh_from_db()
    assert operation.state == HALOperation.State.ACCEPTED
    assert ready_publication.readiness_state == Publication.ReadinessState.PREPROD_VALIDATED
    assert HALSubmissionAttempt.objects.count() == 1
    with pytest.raises(ValueError, match="immutable"):
        attempt.save()


def test_execute_refuses_stale_or_repeated_operation(
    ready_publication, submitter_user
) -> None:
    operation = prepare_preprod_operation(
        publication=ready_publication,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )
    ready_publication.version += 1
    ready_publication.save()
    submitter = Mock()

    with pytest.raises(HALSubmissionError, match="changé"):
        execute_preprod_operation(
            operation=operation,
            actor=submitter_user,
            duplicate_checker=clear_duplicate_check,
            submitter=submitter,
        )

    submitter.assert_not_called()


def test_preprod_button_requires_permission(client, ready_publication, submitter_user) -> None:
    ordinary = get_user_model().objects.create_user(username="ordinary", password="pw")
    client.force_login(ordinary)
    without_permission = client.get(
        reverse("publication-detail", args=[ready_publication.id])
    )
    client.force_login(submitter_user)
    with_permission = client.get(
        reverse("publication-detail", args=[ready_publication.id])
    )

    assert "Préparer le test HAL" not in without_permission.content.decode()
    assert "Préparer le test HAL" in with_permission.content.decode()


def test_execute_view_requires_exact_publication_key_confirmation(
    client, ready_publication, submitter_user
) -> None:
    operation = prepare_preprod_operation(
        publication=ready_publication,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )
    client.force_login(submitter_user)

    with patch("catalog.views.execute_preprod_operation") as execute:
        response = client.post(
            reverse("hal-preprod-execute", args=[operation.id]),
            {"confirmation": "wrong-key"},
        )

    assert response.status_code == 302
    execute.assert_not_called()
