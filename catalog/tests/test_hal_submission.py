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
    HALProductionAttempt,
    HALProductionDeposit,
    HALSubmissionAttempt,
    Publication,
    SourceImport,
    SourceRecord,
)
from catalog.services.hal_credentials import save_credentials
from catalog.services.hal_submission import (
    HALDuplicateError,
    HALSubmissionError,
    check_live_duplicates,
    execute_preprod_operation,
    execute_production_deposit,
    prepare_preprod_operation,
    prepare_production_deposit,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def submitter_user():
    user = get_user_model().objects.create_user(username="hal-submitter", password="pw")
    user.user_permissions.add(Permission.objects.get(codename="submit_hal_preprod"))
    save_credentials(user=user, login="hal-user", password="hal-secret")
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
    assert submitter.call_args.kwargs["login"] == "hal-user"
    assert submitter.call_args.kwargs["password"] == "hal-secret"
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


def test_execute_without_actor_credentials_fails_before_state_change(
    ready_publication, submitter_user
) -> None:
    operation = prepare_preprod_operation(
        publication=ready_publication,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )
    submitter_user.hal_credential.delete()
    submitter = Mock()

    with pytest.raises(HALSubmissionError, match="Mon compte"):
        execute_preprod_operation(
            operation=operation,
            actor=submitter_user,
            duplicate_checker=clear_duplicate_check,
            submitter=submitter,
        )

    operation.refresh_from_db()
    assert operation.state == HALOperation.State.PREPARED
    assert not HALSubmissionAttempt.objects.exists()
    submitter.assert_not_called()


def test_execute_uses_requesting_users_credentials_only(
    ready_publication, submitter_user
) -> None:
    other = get_user_model().objects.create_user(username="other", password="pw")
    save_credentials(user=other, login="other-login", password="other-secret")
    operation = prepare_preprod_operation(
        publication=ready_publication,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )
    submitter = Mock(
        return_value=SWORDResult(
            xml_file="payload.xml",
            status_code=400,
            accepted=False,
            sha256=operation.payload.sha256,
        )
    )

    execute_preprod_operation(
        operation=operation,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
        submitter=submitter,
    )

    assert submitter.call_args.kwargs["login"] == "hal-user"
    assert submitter.call_args.kwargs["password"] == "hal-secret"
    assert "other" not in repr(submitter.call_args)


def test_submission_error_cannot_persist_actor_credentials(
    ready_publication, submitter_user
) -> None:
    operation = prepare_preprod_operation(
        publication=ready_publication,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )

    def leaking_submitter(*args, **kwargs):
        raise RuntimeError("failed for hal-user using hal-secret")

    attempt = execute_preprod_operation(
        operation=operation,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
        submitter=leaking_submitter,
    )

    assert "hal-user" not in attempt.error
    assert "hal-secret" not in attempt.error
    assert attempt.error == "failed for [redacted] using [redacted]"


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


def _accepted_preprod(publication, user):
    operation = prepare_preprod_operation(
        publication=publication,
        actor=user,
        duplicate_checker=clear_duplicate_check,
    )
    execute_preprod_operation(
        operation=operation,
        actor=user,
        duplicate_checker=clear_duplicate_check,
        submitter=Mock(
            return_value=SWORDResult(
                xml_file="payload.xml",
                status_code=202,
                accepted=True,
                sha256=operation.payload.sha256,
            )
        ),
    )
    operation.refresh_from_db()
    return operation


def test_production_deposit_reuses_exact_accepted_payload_and_records_receipt(
    ready_publication, submitter_user
) -> None:
    operation = _accepted_preprod(ready_publication, submitter_user)
    deposit = prepare_production_deposit(
        preprod_operation=operation,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )
    submitter = Mock(
        return_value=SWORDResult(
            xml_file="payload.xml",
            status_code=202,
            accepted=True,
            hal_id="hal-01234567",
            hal_url="https://hal.science/hal-01234567",
            sha256=operation.payload.sha256,
        )
    )

    attempt = execute_production_deposit(
        deposit=deposit,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
        submitter=submitter,
    )

    assert attempt.accepted is True
    assert attempt.payload_id == operation.payload.id
    assert submitter.call_args.kwargs["expected_sha256"] == operation.payload.sha256
    assert submitter.call_args.kwargs["confirmation"] == "SUBMIT_TO_HAL"
    assert submitter.call_args.kwargs["login"] == "hal-user"
    ready_publication.refresh_from_db()
    deposit.refresh_from_db()
    assert ready_publication.hal_id == "hal-01234567"
    assert ready_publication.hal_status == "submitted"
    assert ready_publication.hal_synced_version == ready_publication.version
    assert deposit.state == HALProductionDeposit.State.ACCEPTED
    assert HALProductionAttempt.objects.count() == 1


def test_prepare_production_is_idempotent_for_one_accepted_test(
    ready_publication, submitter_user
) -> None:
    operation = _accepted_preprod(ready_publication, submitter_user)

    first = prepare_production_deposit(
        preprod_operation=operation,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )
    repeated = prepare_production_deposit(
        preprod_operation=operation,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )

    assert repeated.id == first.id
    assert HALProductionDeposit.objects.count() == 1
    assert AuditEvent.objects.filter(action="hal.production.prepared").count() == 1


def test_prepare_production_requires_accepted_test_and_matching_receipt(
    ready_publication, submitter_user
) -> None:
    operation = prepare_preprod_operation(
        publication=ready_publication,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )
    with pytest.raises(HALSubmissionError, match="préproduction"):
        prepare_production_deposit(
            preprod_operation=operation,
            actor=submitter_user,
            duplicate_checker=clear_duplicate_check,
        )

    operation.state = HALOperation.State.ACCEPTED
    operation.save(update_fields=["state", "updated_at"])
    with pytest.raises(HALSubmissionError, match="reçu"):
        prepare_production_deposit(
            preprod_operation=operation,
            actor=submitter_user,
            duplicate_checker=clear_duplicate_check,
        )

    assert not HALProductionDeposit.objects.exists()


def test_prepare_production_refuses_existing_hal_id_and_stale_version(
    ready_publication, submitter_user
) -> None:
    operation = _accepted_preprod(ready_publication, submitter_user)
    ready_publication.hal_id = "hal-existing"
    ready_publication.save(update_fields=["hal_id", "updated_at"])
    with pytest.raises(HALSubmissionError, match="déjà"):
        prepare_production_deposit(
            preprod_operation=operation,
            actor=submitter_user,
            duplicate_checker=clear_duplicate_check,
        )

    ready_publication.hal_id = ""
    ready_publication.version += 1
    ready_publication.save(update_fields=["hal_id", "version", "updated_at"])
    with pytest.raises(HALSubmissionError, match="changé"):
        prepare_production_deposit(
            preprod_operation=operation,
            actor=submitter_user,
            duplicate_checker=clear_duplicate_check,
        )

    assert not HALProductionDeposit.objects.exists()


def test_production_refuses_changed_notice_before_network(
    ready_publication, submitter_user
) -> None:
    operation = _accepted_preprod(ready_publication, submitter_user)
    deposit = prepare_production_deposit(
        preprod_operation=operation,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )
    ready_publication.version += 1
    ready_publication.save()
    submitter = Mock()

    with pytest.raises(HALSubmissionError, match="changé"):
        execute_production_deposit(
            deposit=deposit,
            actor=submitter_user,
            duplicate_checker=clear_duplicate_check,
            submitter=submitter,
        )
    submitter.assert_not_called()
    deposit.refresh_from_db()
    assert deposit.state == HALProductionDeposit.State.PREPARED


@pytest.mark.parametrize("guard", ["existing_hal_id", "invalid_test", "checksum"])
def test_production_guards_fail_before_network_or_state_change(
    ready_publication, submitter_user, guard
) -> None:
    operation = _accepted_preprod(ready_publication, submitter_user)
    deposit = prepare_production_deposit(
        preprod_operation=operation,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )
    if guard == "existing_hal_id":
        ready_publication.hal_id = "hal-existing"
        ready_publication.save(update_fields=["hal_id", "updated_at"])
    elif guard == "invalid_test":
        operation.state = HALOperation.State.REJECTED
        operation.save(update_fields=["state", "updated_at"])
    else:
        deposit.payload_sha256 = "f" * 64
        deposit.save(update_fields=["payload_sha256", "updated_at"])
    submitter = Mock()

    with pytest.raises(HALSubmissionError):
        execute_production_deposit(
            deposit=deposit,
            actor=submitter_user,
            duplicate_checker=clear_duplicate_check,
            submitter=submitter,
        )

    submitter.assert_not_called()
    deposit.refresh_from_db()
    assert deposit.state == HALProductionDeposit.State.PREPARED
    assert not HALProductionAttempt.objects.exists()


def test_production_requires_requesting_users_credentials_before_network(
    ready_publication, submitter_user
) -> None:
    operation = _accepted_preprod(ready_publication, submitter_user)
    deposit = prepare_production_deposit(
        preprod_operation=operation,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )
    submitter_user.hal_credential.delete()
    submitter = Mock()

    with pytest.raises(HALSubmissionError, match="Mon compte"):
        execute_production_deposit(
            deposit=deposit,
            actor=submitter_user,
            duplicate_checker=clear_duplicate_check,
            submitter=submitter,
        )

    submitter.assert_not_called()
    deposit.refresh_from_db()
    assert deposit.state == HALProductionDeposit.State.PREPARED


def test_duplicate_recheck_blocks_production_before_network(
    ready_publication, submitter_user
) -> None:
    operation = _accepted_preprod(ready_publication, submitter_user)
    deposit = prepare_production_deposit(
        preprod_operation=operation,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )
    submitter = Mock()

    def blocked(_publication):
        raise HALDuplicateError("duplicate", check={"blocked": True})

    with pytest.raises(HALDuplicateError):
        execute_production_deposit(
            deposit=deposit,
            actor=submitter_user,
            duplicate_checker=blocked,
            submitter=submitter,
        )

    submitter.assert_not_called()
    deposit.refresh_from_db()
    assert deposit.state == HALProductionDeposit.State.PREPARED


def test_explicit_hal_rejection_records_sanitized_immutable_receipt(
    ready_publication, submitter_user
) -> None:
    operation = _accepted_preprod(ready_publication, submitter_user)
    deposit = prepare_production_deposit(
        preprod_operation=operation,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )

    attempt = execute_production_deposit(
        deposit=deposit,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
        submitter=Mock(
            return_value=SWORDResult(
                xml_file="payload.xml",
                status_code=400,
                accepted=False,
                response_body="Rejected for hal-user",
                error="Bad credentials: hal-secret",
                sha256=operation.payload.sha256,
            )
        ),
    )

    deposit.refresh_from_db()
    ready_publication.refresh_from_db()
    assert deposit.state == HALProductionDeposit.State.REJECTED
    assert attempt.accepted is False
    assert "hal-user" not in attempt.response_body
    assert "hal-secret" not in attempt.error
    assert ready_publication.hal_id == ""
    with pytest.raises(ValueError, match="immutable"):
        attempt.save()


def test_network_uncertainty_blocks_automatic_production_retry(
    ready_publication, submitter_user
) -> None:
    operation = _accepted_preprod(ready_publication, submitter_user)
    deposit = prepare_production_deposit(
        preprod_operation=operation,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )
    submitter = Mock(side_effect=TimeoutError("unknown outcome"))

    attempt = execute_production_deposit(
        deposit=deposit,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
        submitter=submitter,
    )
    deposit.refresh_from_db()
    assert attempt.accepted is False
    assert deposit.state == HALProductionDeposit.State.UNCERTAIN
    with pytest.raises(HALSubmissionError, match="déjà"):
        execute_production_deposit(
            deposit=deposit,
            actor=submitter_user,
            duplicate_checker=clear_duplicate_check,
            submitter=submitter,
        )


def test_accepted_response_without_hal_id_is_not_treated_as_confirmed(
    ready_publication, submitter_user
) -> None:
    operation = _accepted_preprod(ready_publication, submitter_user)
    deposit = prepare_production_deposit(
        preprod_operation=operation,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )

    attempt = execute_production_deposit(
        deposit=deposit,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
        submitter=Mock(
            return_value=SWORDResult(
                xml_file="payload.xml",
                status_code=202,
                accepted=True,
                sha256=operation.payload.sha256,
            )
        ),
    )

    deposit.refresh_from_db()
    ready_publication.refresh_from_db()
    assert attempt.accepted is False
    assert deposit.state == HALProductionDeposit.State.UNCERTAIN
    assert ready_publication.hal_id == ""


def test_production_view_requires_phrase_checkbox_and_permission(
    client, ready_publication, submitter_user
) -> None:
    operation = _accepted_preprod(ready_publication, submitter_user)
    deposit = prepare_production_deposit(
        preprod_operation=operation,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )
    submitter_user.user_permissions.add(
        Permission.objects.get(codename="submit_hal_production")
    )
    client.force_login(submitter_user)

    with patch("catalog.views.execute_production_deposit") as execute:
        client.post(
            reverse("hal-production-execute", args=[deposit.id]),
            {"confirmation": "DÉPOSER SUR HAL"},
        )
    execute.assert_not_called()


def test_production_prepare_view_requires_permission_and_accepted_test(
    client, ready_publication, submitter_user
) -> None:
    client.force_login(submitter_user)
    with patch("catalog.views.prepare_production_deposit") as prepare_mock:
        response = client.post(
            reverse("hal-production-prepare", args=[ready_publication.id]), follow=True
        )
    assert response.status_code == 200
    assert "droit de déposer" in response.content.decode()
    prepare_mock.assert_not_called()

    submitter_user.user_permissions.add(
        Permission.objects.get(codename="submit_hal_production")
    )
    with patch("catalog.views.prepare_production_deposit") as prepare_mock:
        response = client.post(
            reverse("hal-production-prepare", args=[ready_publication.id]), follow=True
        )
    assert response.status_code == 200
    assert "test de préproduction accepté" in response.content.decode()
    prepare_mock.assert_not_called()


def test_production_prepare_and_detail_views_preserve_safe_handoff(
    client, ready_publication, submitter_user
) -> None:
    operation = _accepted_preprod(ready_publication, submitter_user)
    deposit = prepare_production_deposit(
        preprod_operation=operation,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )
    submitter_user.user_permissions.add(
        Permission.objects.get(codename="submit_hal_production")
    )
    client.force_login(submitter_user)

    with patch("catalog.views.prepare_production_deposit", return_value=deposit) as prepare_mock:
        response = client.post(
            reverse("hal-production-prepare", args=[ready_publication.id])
        )

    assert response.status_code == 302
    assert response.url == reverse("hal-production-deposit", args=[deposit.id])
    prepare_mock.assert_called_once_with(preprod_operation=operation, actor=submitter_user)

    detail = client.get(response.url)
    content = detail.content.decode()
    assert detail.status_code == 200
    assert deposit.payload_sha256 in content
    assert "DÉPOSER SUR HAL" in content
    assert 'name="understood" value="yes"' in content


def test_production_execute_view_surfaces_service_error_without_state_change(
    client, ready_publication, submitter_user
) -> None:
    operation = _accepted_preprod(ready_publication, submitter_user)
    deposit = prepare_production_deposit(
        preprod_operation=operation,
        actor=submitter_user,
        duplicate_checker=clear_duplicate_check,
    )
    submitter_user.user_permissions.add(
        Permission.objects.get(codename="submit_hal_production")
    )
    client.force_login(submitter_user)

    with patch(
        "catalog.views.execute_production_deposit",
        side_effect=HALSubmissionError("Le contrôle final a échoué."),
    ) as execute_mock:
        response = client.post(
            reverse("hal-production-execute", args=[deposit.id]),
            {"confirmation": "DÉPOSER SUR HAL", "understood": "yes"},
            follow=True,
        )

    assert response.status_code == 200
    assert "Le contrôle final a échoué." in response.content.decode()
    execute_mock.assert_called_once_with(deposit=deposit, actor=submitter_user)
    deposit.refresh_from_db()
    assert deposit.state == HALProductionDeposit.State.PREPARED

    ordinary = get_user_model().objects.create_user(username="no-production", password="pw")
    client.force_login(ordinary)
    with patch("catalog.views.execute_production_deposit") as execute:
        client.post(
            reverse("hal-production-execute", args=[deposit.id]),
            {"confirmation": "DÉPOSER SUR HAL", "understood": "yes"},
        )
    execute.assert_not_called()
