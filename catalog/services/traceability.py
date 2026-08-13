from __future__ import annotations

from typing import TypedDict

from django.contrib.auth.models import AbstractBaseUser
from django.utils import timezone

from catalog.models import Publication


class TraceabilityEvent(TypedDict):
    created_at: timezone.datetime
    kind: str
    title: str
    description: str
    actor: AbstractBaseUser | None
    reason: str


def traceability_events(publication: Publication) -> list[TraceabilityEvent]:
    """Project immutable domain records into one human-readable activity stream."""
    events: list[TraceabilityEvent] = []
    for removal in publication.hal_removal_records.all():
        events.append(
            {
                "created_at": removal.created_at,
                "kind": "hal",
                "title": "Dissociation de HAL",
                "description": (
                    f"La notice {removal.former_hal_id} a été déclarée supprimée "
                    "directement dans HAL."
                ),
                "actor": removal.actor,
                "reason": removal.reason,
            }
        )
    for decision in publication.decisions.all():
        events.append(
            {
                "created_at": decision.created_at,
                "kind": "decision",
                "title": f"Décision éditoriale · {decision.field_path}",
                "description": (
                    f"{decision.get_outcome_display()} · version "
                    f"{decision.base_version} → {decision.resulting_version}"
                ),
                "actor": decision.actor,
                "reason": decision.reason,
            }
        )
    for link in publication.document_links.all():
        filename = link.summary.source_filename or "Document sans nom"
        events.append(
            {
                "created_at": link.created_at,
                "kind": "document",
                "title": "Document associé",
                "description": f"{filename} · {link.get_action_display()}",
                "actor": link.actor,
                "reason": "",
            }
        )
    for deposit in publication.hal_production_deposits.all():
        events.append(
            {
                "created_at": deposit.created_at,
                "kind": "hal",
                "title": "Dépôt HAL production",
                "description": deposit.get_state_display(),
                "actor": deposit.requested_by,
                "reason": "",
            }
        )
    for operation in publication.hal_operations.all():
        events.append(
            {
                "created_at": operation.created_at,
                "kind": "hal",
                "title": "Test HAL préproduction",
                "description": operation.get_state_display(),
                "actor": operation.requested_by,
                "reason": "",
            }
        )
    for source in publication.source_records.all():
        events.append(
            {
                "created_at": source.created_at,
                "kind": "source",
                "title": "Source importée",
                "description": f"{source.source_import.source_name} · {source.locator}",
                "actor": None,
                "reason": "",
            }
        )
    return sorted(events, key=lambda event: event["created_at"], reverse=True)
