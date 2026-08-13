from __future__ import annotations

from typing import Any

from catalog.models import HALOperation, Publication


def build_hal_journey(
    publication: Publication,
    operation: HALOperation | None,
    *,
    can_submit: bool,
    can_submit_production: bool,
    has_credentials: bool,
) -> dict[str, Any]:
    """Build user-facing milestones from persisted HAL workflow state."""
    on_hal = bool(publication.hal_id)
    published = on_hal and publication.hal_status != "submitted"
    metadata_ready = (
        not publication.missing_required_fields
        and publication.readiness_state
        in {
            Publication.ReadinessState.HAL_READY,
            Publication.ReadinessState.PREPROD_VALIDATED,
            Publication.ReadinessState.PRODUCTION_SUBMITTED,
        }
    )
    latest_attempt = operation.attempts.first() if operation else None
    preprod_accepted = bool(operation and operation.state == HALOperation.State.ACCEPTED)

    steps = [
        {
            "title": "Notice créée",
            "state": "complete",
            "date": publication.created_at,
            "description": "La notice est enregistrée dans l’application.",
        },
        {
            "title": "Minimum HAL",
            "state": "complete" if metadata_ready or on_hal else "current",
            "date": publication.updated_at if metadata_ready else None,
            "description": (
                "Les métadonnées minimales demandées par HAL sont présentes."
                if metadata_ready or on_hal
                else (
                    f"{len(publication.missing_required_fields)} champ(s) "
                    "obligatoire(s) à compléter."
                    if publication.missing_required_fields
                    else "La notice doit encore être vérifiée avant le test HAL."
                )
            ),
            "action": "complete_metadata" if not metadata_ready and not on_hal else "",
        },
    ]

    if on_hal and operation is None:
        steps.extend(
            [
                {
                    "title": "Contrôle des doublons",
                    "state": "external",
                    "description": "Étape réalisée hors de cette application.",
                },
                {
                    "title": "Test en préproduction",
                    "state": "external",
                    "description": "Aucun test historique n’est enregistré ici.",
                },
            ]
        )
    else:
        steps.append(
            {
                "title": "Contrôle des doublons",
                "state": "complete" if operation else ("current" if metadata_ready else "future"),
                "date": operation.created_at if operation else None,
                "description": (
                    "Le contrôle multi-champs a été effectué lors de la préparation du test."
                    if operation
                    else "HAL sera interrogé avant de préparer le test."
                ),
                "action": "prepare_test" if metadata_ready and not operation and can_submit else "",
            }
        )
        if not operation:
            preprod_state = "future"
            preprod_description = "Cette étape sera débloquée après le contrôle des doublons."
            preprod_action = ""
        elif preprod_accepted:
            preprod_state = "complete"
            preprod_description = "HAL préproduction a accepté la notice de test."
            preprod_action = "view_history"
        elif operation.state == HALOperation.State.PREPARED:
            if not can_submit:
                preprod_state = "blocked"
                preprod_description = "Votre compte n’a pas l’autorisation d’envoyer ce test."
                preprod_action = ""
            else:
                preprod_state = "current" if has_credentials else "blocked"
                preprod_description = (
                    "Le test est prêt à être envoyé avec vos identifiants HAL."
                    if has_credentials
                    else "Ajoutez vos identifiants HAL personnels pour envoyer le test."
                )
                preprod_action = "resume_test" if has_credentials else "configure_credentials"
        elif operation.state == HALOperation.State.SUBMITTING:
            preprod_state = "current"
            preprod_description = "L’envoi du test est en cours."
            preprod_action = "view_history"
        else:
            preprod_state = "blocked"
            preprod_description = (
                "Le dernier test a échoué. Préparez un nouveau contrôle après correction."
            )
            preprod_action = "prepare_test" if can_submit else "view_history"
        steps.append(
            {
                "title": "Test en préproduction",
                "state": preprod_state,
                "date": (
                    latest_attempt.created_at
                    if latest_attempt
                    else operation.created_at
                    if operation
                    else None
                ),
                "description": preprod_description,
                "action": preprod_action,
            }
        )

    steps.append(
        {
            "title": "Publication sur HAL",
            "state": (
                "complete"
                if on_hal
                else "current"
                if preprod_accepted and can_submit_production
                else "blocked"
                if preprod_accepted
                else "future"
            ),
            "date": None,
            "description": (
                (
                    f"La notice est publiée sous l’identifiant {publication.hal_id}."
                    if published
                    else f"Le dépôt {publication.hal_id} a été transmis et attend son statut HAL."
                )
                if on_hal
                else (
                    "Le XML testé peut maintenant être préparé pour le dépôt réel."
                    if can_submit_production
                    else "Une autorisation de dépôt HAL production est requise."
                    if preprod_accepted
                    else "Le dépôt réel sera débloqué après validation en préproduction."
                )
            ),
            "action": (
                "open_hal"
                if on_hal
                else "prepare_production"
                if preprod_accepted and can_submit_production
                else ""
            ),
        }
    )
    completed = sum(step["state"] == "complete" for step in steps)
    return {
        "steps": steps,
        "completed": completed,
        "total": len(steps),
        "published": on_hal,
    }
