from __future__ import annotations

from django.db import transaction
from django.utils.translation import gettext as _

from catalog.models import AuditEvent, HALRemovalRecord, Publication
from catalog.services.publication_readiness import recalculate_hal_readiness


class HALReconciliationError(ValueError):
    pass


@transaction.atomic
def mark_removed_from_hal(
    *,
    publication: Publication,
    actor,
    confirmed_hal_id: str,
    reason: str,
    remote_removal_confirmed: bool,
) -> HALRemovalRecord:
    """Reconcile local state after a user has removed the notice in HAL itself.

    This function performs no network operation and can never delete from HAL.
    """
    locked = Publication.objects.select_for_update().get(pk=publication.pk)
    former_hal_id = locked.hal_id.strip()
    if not former_hal_id:
        raise HALReconciliationError(_("Cette notice n’a pas d’identifiant HAL actif."))
    if not remote_removal_confirmed:
        raise HALReconciliationError(
            _("Confirmez que la suppression a déjà été effectuée dans l’interface HAL.")
        )
    if confirmed_hal_id.strip() != former_hal_id:
        raise HALReconciliationError(_("L’identifiant HAL saisi ne correspond pas."))
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise HALReconciliationError(_("Indiquez la raison de cette remise en brouillon."))

    record = HALRemovalRecord.objects.create(
        publication=locked,
        former_hal_id=former_hal_id,
        former_hal_status=locked.hal_status,
        actor=actor,
        reason=cleaned_reason,
    )
    previous_version = locked.version
    locked.hal_id = ""
    locked.hal_status = "removed_from_hal"
    locked.hal_synced_version = None
    recalculate_hal_readiness(locked)
    # Every payload and preproduction decision belongs to the preceding cycle.
    locked.version += 1
    locked.save(
        update_fields=[
            "hal_id",
            "hal_status",
            "hal_synced_version",
            "missing_required_fields",
            "readiness_state",
            "version",
            "updated_at",
        ]
    )
    AuditEvent.objects.create(
        actor=actor,
        action="hal.removal.reconciled",
        object_type="publication",
        object_id=str(locked.id),
        metadata={
            "former_hal_id": former_hal_id,
            "former_hal_status": record.former_hal_status,
            "reason": cleaned_reason,
            "confirmation_method": record.confirmation_method,
            "previous_version": previous_version,
            "resulting_version": locked.version,
            "network_operation": False,
        },
    )
    return record
