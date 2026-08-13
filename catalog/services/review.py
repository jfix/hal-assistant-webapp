from __future__ import annotations

from datetime import date, datetime
from typing import Any

from django.contrib.auth.models import AbstractBaseUser
from django.db import IntegrityError, transaction

from catalog.models import AssertionDecision, AuditEvent, FieldAssertion, Publication
from catalog.services.imports import MATERIALIZED_FIELDS, coerce_field_value
from catalog.services.publication_readiness import recalculate_hal_readiness


def _json_safe(value: Any) -> Any:
    """Make a materialized value safe to store in a JSON decision column."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


# Bibliographic fields a reviewer may edit directly. Identity fields (doi,
# hal_id), workflow/state fields, and controlled-vocabulary types are excluded:
# they drive de-duplication, readiness, and XML generation.
EDITABLE_FIELDS: tuple[str, ...] = (
    "title",
    "publication_year",
    "language",
    "abstract_en",
    "abstract_fr",
    "keywords_en",
    "keywords_fr",
    "pages",
    "authors",
    "editors",
    "journal_title",
    "book_title",
    "publisher",
    "publisher_city",
    "volume",
    "issue",
    "isbn",
    "issn",
    "conference_title",
    "conference_start_date",
    "conference_end_date",
    "conference_city",
    "conference_country",
    "source_url",
)


class ReviewError(Exception):
    """A decision cannot be applied because of the assertion's state."""


class ReviewConflict(Exception):
    """The publication changed since the reviewer loaded it (optimistic lock)."""


def pending_proposals(publication: Publication) -> list[FieldAssertion]:
    """Proposed assertions for a publication that have no decision yet."""
    return list(
        publication.assertions.filter(state=FieldAssertion.State.PROPOSED)
        .filter(decision__isnull=True)
        .order_by("field_path", "-created_at")
    )


@transaction.atomic
def decide_assertion(
    *,
    assertion: FieldAssertion,
    actor: AbstractBaseUser | None,
    outcome: str,
    base_version: int,
    reason: str = "",
    edited_value: str | None = None,
) -> AssertionDecision:
    """Resolve one proposed field assertion.

    Three outcomes: ``accepted`` materializes the proposed value, ``edited``
    materializes a reviewer-supplied value, and ``rejected`` keeps the current
    value. Materializing bumps the publication version. Every outcome is guarded
    by an optimistic version check and recorded as an append-only decision plus
    an audit event.
    """
    if outcome not in AssertionDecision.Outcome.values:
        raise ReviewError(f"Décision inconnue : {outcome}")
    if assertion.field_path not in MATERIALIZED_FIELDS:
        raise ReviewError(f"Champ non révisable : {assertion.field_path}")
    if assertion.state != FieldAssertion.State.PROPOSED:
        raise ReviewError("Seules les modifications proposées peuvent être décidées.")
    if outcome == AssertionDecision.Outcome.EDITED and edited_value is None:
        raise ReviewError("Une valeur modifiée est requise.")

    # Lock the publication row for the duration of the decision.
    publication = Publication.objects.select_for_update().get(pk=assertion.publication_id)
    if publication.version != base_version:
        raise ReviewConflict(
            "Cette notice a changé depuis son chargement. "
            "Rechargez la page et vérifiez les valeurs actuelles."
        )

    previous_value = _json_safe(getattr(publication, assertion.field_path))
    if outcome == AssertionDecision.Outcome.ACCEPTED:
        applied_value = assertion.value
    elif outcome == AssertionDecision.Outcome.EDITED:
        applied_value = coerce_field_value(assertion.field_path, edited_value)
    else:  # rejected
        applied_value = None

    resulting_version = publication.version
    if applied_value is not None or outcome == AssertionDecision.Outcome.EDITED:
        setattr(publication, assertion.field_path, applied_value)
        resulting_version = publication.version + 1
        publication.version = resulting_version
        recalculate_hal_readiness(publication)
        publication.save(
            update_fields=[
                assertion.field_path,
                "version",
                "missing_required_fields",
                "readiness_state",
                "updated_at",
            ]
        )

    try:
        decision = AssertionDecision.objects.create(
            publication=publication,
            assertion=assertion,
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            field_path=assertion.field_path,
            outcome=outcome,
            previous_value=previous_value,
            applied_value=applied_value,
            reason=reason.strip(),
            base_version=base_version,
            resulting_version=resulting_version,
        )
    except IntegrityError as exc:  # one decision per assertion (OneToOne)
        raise ReviewError("Cette modification a déjà été décidée.") from exc

    AuditEvent.objects.create(
        actor=decision.actor,
        action=f"assertion.{outcome}",
        object_type="field_assertion",
        object_id=str(assertion.id),
        metadata={
            "publication_key": publication.publication_key,
            "field_path": assertion.field_path,
            "base_version": base_version,
            "resulting_version": resulting_version,
            "applied_value": applied_value,
        },
    )
    return decision


@transaction.atomic
def edit_field(
    *,
    publication: Publication,
    field_path: str,
    actor: AbstractBaseUser | None,
    edited_value: str,
    base_version: int,
    reason: str = "",
) -> AssertionDecision:
    """Edit one bibliographic field directly, without a prior proposal.

    Recorded exactly like a proposal decision (append-only, audited, optimistic
    version check) but with no linked assertion. Only fields in EDITABLE_FIELDS
    may be changed. Never contacts HAL.
    """
    if field_path not in EDITABLE_FIELDS:
        raise ReviewError(f"Champ non modifiable : {field_path}")

    locked = Publication.objects.select_for_update().get(pk=publication.pk)
    if locked.version != base_version:
        raise ReviewConflict(
            "Cette notice a changé depuis son chargement. "
            "Rechargez la page et vérifiez les valeurs actuelles."
        )

    previous_value = _json_safe(getattr(locked, field_path))
    applied_value = coerce_field_value(field_path, edited_value)
    resulting_version = locked.version + 1
    setattr(locked, field_path, applied_value)
    locked.version = resulting_version
    recalculate_hal_readiness(locked)
    locked.save(
        update_fields=[
            field_path,
            "version",
            "missing_required_fields",
            "readiness_state",
            "updated_at",
        ]
    )

    decision = AssertionDecision.objects.create(
        publication=locked,
        assertion=None,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        field_path=field_path,
        outcome=AssertionDecision.Outcome.EDITED,
        previous_value=previous_value,
        applied_value=applied_value,
        reason=reason.strip(),
        base_version=base_version,
        resulting_version=resulting_version,
    )
    AuditEvent.objects.create(
        actor=decision.actor,
        action="field.edited",
        object_type="publication",
        object_id=str(locked.id),
        metadata={
            "publication_key": locked.publication_key,
            "field_path": field_path,
            "base_version": base_version,
            "resulting_version": resulting_version,
            "applied_value": applied_value,
        },
    )
    return decision
