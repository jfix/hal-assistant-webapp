from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from catalog.models import HALOperation, Publication
from catalog.services.hal_journey import build_hal_journey

pytestmark = pytest.mark.django_db


@pytest.fixture
def journey_publication() -> Publication:
    return Publication.objects.create(
        publication_key="journey-direct",
        publication_type="journal_article",
        title="A journey through HAL",
        readiness_state=Publication.ReadinessState.HAL_READY,
        missing_required_fields=[],
    )


def operation_for(publication: Publication, state: str) -> HALOperation:
    actor = get_user_model().objects.create_user(
        username=f"journey-{state}", password="pw"
    )
    return HALOperation.objects.create(
        publication=publication,
        requested_by=actor,
        publication_version=publication.version,
        state=state,
        duplicate_check={},
    )


@pytest.mark.parametrize(
    ("can_submit", "has_credentials", "expected_state", "expected_action"),
    [
        (False, False, "blocked", ""),
        (True, False, "blocked", "configure_credentials"),
        (True, True, "current", "resume_test"),
    ],
)
def test_prepared_test_requires_permission_and_personal_credentials(
    journey_publication,
    can_submit,
    has_credentials,
    expected_state,
    expected_action,
) -> None:
    operation = operation_for(journey_publication, HALOperation.State.PREPARED)

    journey = build_hal_journey(
        journey_publication,
        operation,
        can_submit=can_submit,
        can_submit_production=False,
        has_credentials=has_credentials,
    )

    preprod = journey["steps"][3]
    assert preprod["state"] == expected_state
    assert preprod["action"] == expected_action


def test_rejected_test_returns_to_preparation_for_authorized_user(
    journey_publication,
) -> None:
    operation = operation_for(journey_publication, HALOperation.State.REJECTED)

    journey = build_hal_journey(
        journey_publication,
        operation,
        can_submit=True,
        can_submit_production=False,
        has_credentials=True,
    )

    preprod = journey["steps"][3]
    assert preprod["state"] == "blocked"
    assert preprod["action"] == "prepare_test"
    assert "échoué" in preprod["description"]


@pytest.mark.parametrize(
    ("can_submit_production", "expected_state", "expected_action"),
    [(False, "blocked", ""), (True, "current", "prepare_production")],
)
def test_accepted_test_gates_real_deposit_permission(
    journey_publication,
    can_submit_production,
    expected_state,
    expected_action,
) -> None:
    operation = operation_for(journey_publication, HALOperation.State.ACCEPTED)

    journey = build_hal_journey(
        journey_publication,
        operation,
        can_submit=True,
        can_submit_production=can_submit_production,
        has_credentials=True,
    )

    production = journey["steps"][4]
    assert production["state"] == expected_state
    assert production["action"] == expected_action


def test_recent_deposit_is_distinguished_from_published_import(
    journey_publication,
) -> None:
    journey_publication.hal_id = "hal-01234567"
    journey_publication.hal_status = "submitted"
    journey_publication.save(update_fields=["hal_id", "hal_status", "updated_at"])

    journey = build_hal_journey(
        journey_publication,
        None,
        can_submit=False,
        can_submit_production=False,
        has_credentials=False,
    )

    production = journey["steps"][4]
    assert journey["published"] is True
    assert production["state"] == "complete"
    assert "attend son statut HAL" in production["description"]
