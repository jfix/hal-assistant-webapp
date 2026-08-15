from __future__ import annotations

from typing import TypedDict

from django.contrib.auth.models import AbstractBaseUser
from django.utils import timezone
from django.utils.translation import gettext as _

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
                "title": _("Dissociation de HAL"),
                "description": (
                    _("La notice %(hal_id)s a été déclarée supprimée directement dans HAL.")
                    % {"hal_id": removal.former_hal_id}
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
                "title": _("Décision éditoriale · %(field)s") % {"field": decision.field_path},
                "description": (
                    _("%(outcome)s · version %(base)s → %(resulting)s")
                    % {
                        "outcome": decision.get_outcome_display(),
                        "base": decision.base_version,
                        "resulting": decision.resulting_version,
                    }
                ),
                "actor": decision.actor,
                "reason": decision.reason,
            }
        )
    for link in publication.document_links.all():
        filename = link.summary.source_filename or _("Document sans nom")
        events.append(
            {
                "created_at": link.created_at,
                "kind": "document",
                "title": _("Document associé"),
                "description": _("%(filename)s · %(action)s")
                % {"filename": filename, "action": link.get_action_display()},
                "actor": link.actor,
                "reason": "",
            }
        )
    for deposit in publication.hal_production_deposits.all():
        events.append(
            {
                "created_at": deposit.created_at,
                "kind": "hal",
                "title": _("Dépôt HAL production"),
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
                "title": _("Test HAL préproduction"),
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
                "title": _("Source importée"),
                "description": _("%(source_name)s · %(locator)s")
                % {"source_name": source.source_import.source_name, "locator": source.locator},
                "actor": None,
                "reason": "",
            }
        )
    return sorted(events, key=lambda event: event["created_at"], reverse=True)
