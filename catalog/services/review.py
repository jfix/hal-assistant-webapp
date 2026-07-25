from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser
from django.db import IntegrityError, transaction

from catalog.models import AssertionDecision, AuditEvent, FieldAssertion, Publication
from catalog.services.imports import MATERIALIZED_FIELDS, coerce_field_value


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
        publication.save(update_fields=[assertion.field_path, "version", "updated_at"])

    try:
        decision = AssertionDecision.objects.create(
            publication=publication,
            assertion=assertion,
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            field_path=assertion.field_path,
            outcome=outcome,
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
