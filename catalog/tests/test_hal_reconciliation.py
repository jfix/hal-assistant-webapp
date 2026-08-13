from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from catalog.models import AuditEvent, HALRemovalRecord, Publication, SourceImport, SourceRecord
from catalog.services.hal_reconciliation import HALReconciliationError, mark_removed_from_hal

pytestmark = pytest.mark.django_db


@pytest.fixture
def published_publication() -> Publication:
    publication = Publication.objects.create(
        publication_key="removed-hal-record",
        publication_type="journal_article",
        hal_document_type="ART",
        title="A mistakenly deposited article",
        publication_year=2025,
        language="en",
        authors=["Ada Lovelace"],
        journal_title="Journal of Safe Deposits",
        hal_id="hal-01234567",
        hal_status="submitted",
        readiness_state=Publication.ReadinessState.PRODUCTION_SUBMITTED,
        version=4,
        hal_synced_version=4,
    )
    source_import = SourceImport.objects.create(
        source_type=SourceImport.SourceType.MANUAL,
        source_name="manual-entry",
        stored_file="manual/removed-hal-record.json",
        file_sha256="7" * 64,
        parser_version="test",
        report_sha256="8" * 64,
        record_count=1,
        report={},
        retrieved_at=timezone.now(),
    )
    SourceRecord.objects.create(
        source_import=source_import,
        publication=publication,
        locator="manual:removed-hal-record",
        raw_data={
            "title": publication.title,
            "document_type": "ART",
            "year": 2025,
            "language": "en",
            "authors": "Ada Lovelace",
            "container_title": publication.journal_title,
            "hal_domain": "shs.litt",
            "idhal": "ada-lovelace",
        },
        record_sha256="9" * 64,
    )
    return publication


def test_reconciliation_preserves_former_id_and_resets_workflow(
    published_publication,
) -> None:
    actor = get_user_model().objects.create_user(username="reconciler", password="pw")

    record = mark_removed_from_hal(
        publication=published_publication,
        actor=actor,
        confirmed_hal_id="hal-01234567",
        reason="Dépôt réalisé par erreur.",
        remote_removal_confirmed=True,
    )

    published_publication.refresh_from_db()
    assert record.former_hal_id == "hal-01234567"
    assert record.former_hal_status == "submitted"
    assert published_publication.hal_id == ""
    assert published_publication.hal_status == "removed_from_hal"
    assert published_publication.hal_synced_version is None
    assert published_publication.version == 5
    assert published_publication.readiness_state == Publication.ReadinessState.HAL_READY
    assert published_publication.workflow_statuses[0]["label"] == "Brouillon"
    event = AuditEvent.objects.get(action="hal.removal.reconciled")
    assert event.metadata["former_hal_id"] == "hal-01234567"
    assert event.metadata["network_operation"] is False
    with pytest.raises(ValueError, match="immutable"):
        record.save()


@pytest.mark.parametrize(
    ("confirmed_id", "reason", "remote_confirmed"),
    [
        ("wrong-id", "Erreur", True),
        ("hal-01234567", "", True),
        ("hal-01234567", "Erreur", False),
    ],
)
def test_reconciliation_fails_closed_without_all_confirmations(
    published_publication, confirmed_id, reason, remote_confirmed
) -> None:
    actor = get_user_model().objects.create_user(username="reconciler", password="pw")

    with pytest.raises(HALReconciliationError):
        mark_removed_from_hal(
            publication=published_publication,
            actor=actor,
            confirmed_hal_id=confirmed_id,
            reason=reason,
            remote_removal_confirmed=remote_confirmed,
        )

    published_publication.refresh_from_db()
    assert published_publication.hal_id == "hal-01234567"
    assert not HALRemovalRecord.objects.exists()


def test_reconciliation_view_requires_review_permission(
    client, published_publication
) -> None:
    ordinary = get_user_model().objects.create_user(username="ordinary", password="pw")
    client.force_login(ordinary)
    response = client.post(
        reverse("hal-reconcile-removal", args=[published_publication.id]),
        {
            "confirmed_hal_id": published_publication.hal_id,
            "reason": "Erreur",
            "remote_removal_confirmed": "yes",
        },
    )
    assert response.status_code == 302
    published_publication.refresh_from_db()
    assert published_publication.hal_id == "hal-01234567"


def test_detail_exposes_local_only_action_and_then_history(
    client, published_publication
) -> None:
    reviewer = get_user_model().objects.create_user(username="reviewer", password="pw")
    reviewer.user_permissions.add(Permission.objects.get(codename="review_publication"))
    client.force_login(reviewer)

    before = client.get(reverse("publication-detail", args=[published_publication.id]))
    before_content = before.content.decode()
    assert "Cette action ne supprime rien dans HAL" in before_content
    assert before_content.index("Métadonnées structurées") < before_content.index(
        "Gestion exceptionnelle de la présence sur HAL"
    )

    response = client.post(
        reverse("hal-reconcile-removal", args=[published_publication.id]),
        {
            "confirmed_hal_id": published_publication.hal_id,
            "reason": "Dépôt réalisé par erreur.",
            "remote_removal_confirmed": "yes",
        },
        follow=True,
    )
    content = response.content.decode()
    assert "Ancienne présence sur HAL" in content
    assert "hal-01234567" in content
    assert "Marquer comme supprimée de HAL" not in content
