from __future__ import annotations

from catalog.integrations.hal_assistant import readiness_for_publication
from catalog.models import Publication


def recalculate_hal_readiness(publication: Publication) -> None:
    """Apply the reusable package's current HAL-minimum result to a publication."""
    ready, missing, _document_type = readiness_for_publication(publication)
    publication.missing_required_fields = missing
    publication.readiness_state = (
        Publication.ReadinessState.HAL_READY
        if ready
        else Publication.ReadinessState.NEEDS_ENRICHMENT
    )
