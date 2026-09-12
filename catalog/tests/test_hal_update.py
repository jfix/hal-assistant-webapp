from __future__ import annotations

import io
import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone
from hal_assistant.update import UpdateResult

from catalog.models import (
    AuditEvent,
    HALUpdateOperation,
    Publication,
    SourceImport,
    SourceRecord,
)
from catalog.services.hal_credentials import save_credentials
from catalog.services.hal_update import (
    HALUpdateError,
    execute_update,
    execute_update_test,
    fetch_hal_document_version,
    prepare_update_operation,
)

pytestmark = pytest.mark.django_db

HAL_ID = "hal-01234567"


@pytest.fixture
def updater_user():
    user = get_user_model().objects.create_user(username="hal-updater", password="pw")
    user.user_permissions.add(Permission.objects.get(codename="update_hal_production"))
    save_credentials(user=user, login="hal-user", password="hal-secret")
    return user


@pytest.fixture
def modified_publication() -> Publication:
    publication = Publication.objects.create(
        publication_key="update-1",
        publication_type="journal_article",
        hal_document_type="ART",
        title="Archives and memory",
        publication_year=2024,
        authors=["Ada Lovelace"],
        readiness_state=Publication.ReadinessState.HAL_READY,
        hal_id=HAL_ID,
        hal_status="accepted",
        version=3,
        hal_synced_version=2,
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


def fixed_version(_hal_id: str) -> int:
    return 4


def _accepting_updater(calls: list[dict] | None = None):
    def updater(path, **kwargs):
        if calls is not None:
            calls.append({"path": path, **kwargs})
        return UpdateResult(
            hal_id=kwargs["hal_id"],
            version=kwargs["hal_version"],
            xml_file="payload.xml",
            sha256=kwargs.get("expected_sha256") or "",
            target_url=f"https://api.example/sword/hal/{kwargs['hal_id']}v{kwargs['hal_version']}",
            status_code=200,
            accepted=True,
            returned_hal_id=(
                "FooTestId-1" if kwargs["test"] else kwargs["hal_id"]
            ),
        )

    return updater


def _refusing_updater(status_code: int | None = 400):
    def updater(path, **kwargs):
        return UpdateResult(
            hal_id=kwargs["hal_id"],
            version=kwargs["hal_version"],
            xml_file="payload.xml",
            sha256=kwargs.get("expected_sha256") or "",
            target_url="https://api.example/sword/hal/x",
            status_code=status_code,
            accepted=False,
            error="refused",
        )

    return updater


def _prepared(publication, actor) -> HALUpdateOperation:
    return prepare_update_operation(
        publication=publication, actor=actor, version_fetcher=fixed_version
    )


def test_prepare_requires_hal_id_and_local_changes(updater_user) -> None:
    publication = Publication.objects.create(
        publication_key="update-guards",
        publication_type="journal_article",
        title="No HAL id",
        version=2,
        hal_synced_version=2,
    )
    with pytest.raises(HALUpdateError, match="identifiant HAL"):
        prepare_update_operation(
            publication=publication, actor=updater_user, version_fetcher=fixed_version
        )

    publication.hal_id = HAL_ID
    publication.save(update_fields=["hal_id"])
    with pytest.raises(HALUpdateError, match="déjà à jour"):
        prepare_update_operation(
            publication=publication, actor=updater_user, version_fetcher=fixed_version
        )


def test_prepare_freezes_payload_and_is_idempotent(
    modified_publication, updater_user
) -> None:
    operation = _prepared(modified_publication, updater_user)
    repeated = _prepared(modified_publication, updater_user)

    assert repeated.id == operation.id
    assert operation.state == HALUpdateOperation.State.PREPARED
    assert operation.hal_id == HAL_ID
    assert operation.hal_document_version == 4
    assert operation.publication_version == 3
    assert len(operation.payload_sha256) == 64
    assert "<TEI" in operation.payload_content
    assert AuditEvent.objects.filter(action="hal.update.prepared").count() == 1


def test_fetch_hal_document_version_fails_closed() -> None:
    def opener_for(payload: dict):
        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def opener(url, timeout):
            return _Resp(json.dumps(payload).encode())

        return opener

    good = {"response": {"docs": [{"halId_s": HAL_ID, "version_i": 5}]}}
    assert fetch_hal_document_version(HAL_ID, opener=opener_for(good)) == 5

    with pytest.raises(HALUpdateError, match="introuvable"):
        fetch_hal_document_version(HAL_ID, opener=opener_for({"response": {"docs": []}}))

    bad_version = {"response": {"docs": [{"halId_s": HAL_ID, "version_i": "x"}]}}
    with pytest.raises(HALUpdateError, match="version exploitable"):
        fetch_hal_document_version(HAL_ID, opener=opener_for(bad_version))


def test_update_test_accepts_and_advances_state(
    modified_publication, updater_user
) -> None:
    operation = _prepared(modified_publication, updater_user)
    calls: list[dict] = []

    attempt = execute_update_test(
        operation=operation, actor=updater_user, updater=_accepting_updater(calls)
    )

    operation.refresh_from_db()
    assert attempt.accepted is True
    assert attempt.test_mode is True
    assert operation.state == HALUpdateOperation.State.TEST_ACCEPTED
    call = calls[0]
    assert call["test"] is True
    assert call["hal_id"] == HAL_ID
    assert call["hal_version"] == 4
    assert call["login"] == "hal-user"
    assert call["confirmation"] is None

    with pytest.raises(HALUpdateError, match="déjà été exécuté"):
        execute_update_test(
            operation=operation, actor=updater_user, updater=_accepting_updater()
        )


def test_update_test_refusal_rejects_operation(
    modified_publication, updater_user
) -> None:
    operation = _prepared(modified_publication, updater_user)

    attempt = execute_update_test(
        operation=operation, actor=updater_user, updater=_refusing_updater()
    )

    operation.refresh_from_db()
    assert attempt.accepted is False
    assert operation.state == HALUpdateOperation.State.REJECTED


def test_update_test_refuses_stale_publication_version(
    modified_publication, updater_user
) -> None:
    operation = _prepared(modified_publication, updater_user)
    Publication.objects.filter(pk=modified_publication.pk).update(version=4)

    with pytest.raises(HALUpdateError, match="a changé depuis la préparation"):
        execute_update_test(
            operation=operation, actor=updater_user, updater=_accepting_updater()
        )


def test_execute_requires_accepted_test(modified_publication, updater_user) -> None:
    operation = _prepared(modified_publication, updater_user)

    with pytest.raises(HALUpdateError, match="test de mise à jour accepté"):
        execute_update(
            operation=operation, actor=updater_user, updater=_accepting_updater()
        )


def test_execute_updates_record_and_marks_synced(
    modified_publication, updater_user
) -> None:
    operation = _prepared(modified_publication, updater_user)
    execute_update_test(
        operation=operation, actor=updater_user, updater=_accepting_updater()
    )
    calls: list[dict] = []

    attempt = execute_update(
        operation=operation, actor=updater_user, updater=_accepting_updater(calls)
    )

    operation.refresh_from_db()
    modified_publication.refresh_from_db()
    assert attempt.accepted is True
    assert attempt.test_mode is False
    assert operation.state == HALUpdateOperation.State.ACCEPTED
    assert modified_publication.hal_synced_version == 3
    call = calls[0]
    assert call["test"] is False
    assert call["confirmation"] == "UPDATE_EXISTING_HAL_RECORDS"
    assert call["expected_sha256"] == operation.payload_sha256

    with pytest.raises(HALUpdateError, match="test de mise à jour accepté"):
        execute_update(
            operation=operation, actor=updater_user, updater=_accepting_updater()
        )


def test_execute_network_failure_is_uncertain_and_keeps_sync_version(
    modified_publication, updater_user
) -> None:
    operation = _prepared(modified_publication, updater_user)
    execute_update_test(
        operation=operation, actor=updater_user, updater=_accepting_updater()
    )

    attempt = execute_update(
        operation=operation, actor=updater_user, updater=_refusing_updater(status_code=None)
    )

    operation.refresh_from_db()
    modified_publication.refresh_from_db()
    assert attempt.accepted is False
    assert operation.state == HALUpdateOperation.State.UNCERTAIN
    assert modified_publication.hal_synced_version == 2


def test_views_gate_on_permission_and_confirmation(
    client, modified_publication, updater_user
) -> None:
    bystander = get_user_model().objects.create_user(username="bystander", password="pw")
    client.force_login(bystander)
    response = client.post(
        reverse("hal-update-prepare", args=[modified_publication.id])
    )
    assert response.status_code == 302
    assert HALUpdateOperation.objects.count() == 0

    client.force_login(updater_user)
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "catalog.views.prepare_update_operation",
            lambda *, publication, actor: _prepared(publication, actor),
        )
        response = client.post(
            reverse("hal-update-prepare", args=[modified_publication.id])
        )
    operation = HALUpdateOperation.objects.get()
    assert response.status_code == 302
    assert response.url == reverse("hal-update-operation", args=[operation.id])

    detail = client.get(reverse("hal-update-operation", args=[operation.id]))
    content = detail.content.decode()
    assert operation.hal_id in content
    assert operation.payload_sha256 in content

    execute_update_test(
        operation=operation, actor=updater_user, updater=_accepting_updater()
    )
    response = client.post(
        reverse("hal-update-execute", args=[operation.id]),
        {"confirmation": "wrong", "understood": "yes"},
    )
    operation.refresh_from_db()
    assert operation.state == HALUpdateOperation.State.TEST_ACCEPTED


def test_detail_page_offers_update_to_permitted_user(
    client, modified_publication, updater_user
) -> None:
    client.force_login(updater_user)
    content = client.get(
        reverse("publication-detail", args=[modified_publication.id])
    ).content.decode()
    assert "Mettre à jour la notice sur HAL" in content

    Publication.objects.filter(pk=modified_publication.pk).update(hal_synced_version=3)
    content = client.get(
        reverse("publication-detail", args=[modified_publication.id])
    ).content.decode()
    assert "Mettre à jour la notice sur HAL" not in content
