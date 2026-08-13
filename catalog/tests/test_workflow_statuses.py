from __future__ import annotations

import pytest

from catalog.models import Publication

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("hal_id", "hal_status", "readiness", "missing", "version", "synced", "expected"),
    [
        ("", "", Publication.ReadinessState.PARSED, ["authors"], 1, None,
         [("HAL", "Brouillon", "neutral"), ("Données", "À compléter", "warning"),
          ("Synchronisation", "Jamais synchronisé", "neutral")]),
        ("", "", Publication.ReadinessState.HAL_READY, [], 1, None,
         [("HAL", "Brouillon", "neutral"), ("Données", "Prêt pour HAL", "ready"),
          ("Synchronisation", "Jamais synchronisé", "neutral")]),
        ("hal-01234567", "existing", Publication.ReadinessState.HAL_READY, [], 4, 4,
         [("HAL", "Publié sur HAL", "published"),
          ("Données", "Minimum HAL atteint", "ready"),
          ("Synchronisation", "À jour", "synced")]),
        ("hal-01234567", "existing", Publication.ReadinessState.HAL_READY, [], 5, 4,
         [("HAL", "Publié sur HAL", "published"),
          ("Données", "Prêt pour mise à jour HAL", "ready"),
          ("Synchronisation", "Modifié", "warning")]),
        ("hal-01234567", "existing", Publication.ReadinessState.NEEDS_ENRICHMENT,
         ["abstract_fr"], 5, 4,
         [("HAL", "Publié sur HAL", "published"),
          ("Données", "Mise à jour à compléter", "warning"),
          ("Synchronisation", "Modifié", "warning")]),
        ("hal-01234567", "submitted", Publication.ReadinessState.PRODUCTION_SUBMITTED,
         [], 2, None,
         [("HAL", "Déposé sur HAL", "published"),
          ("Données", "Minimum HAL atteint", "ready"),
          ("Synchronisation", "À vérifier", "warning")]),
    ],
)
def test_workflow_status_truth_table(
    hal_id, hal_status, readiness, missing, version, synced, expected
) -> None:
    publication = Publication.objects.create(
        publication_key=f"workflow-{Publication.objects.count()}",
        publication_type="journal_article",
        title="Workflow characterization",
        hal_id=hal_id,
        hal_status=hal_status,
        readiness_state=readiness,
        missing_required_fields=missing,
        version=version,
        hal_synced_version=synced,
    )

    observed = [
        (status["dimension"], status["label"], status["tone"])
        for status in publication.workflow_statuses
    ]

    assert observed == expected
    assert [status["dimension"] for status in publication.workflow_statuses] == [
        "HAL", "Données", "Synchronisation"
    ]
