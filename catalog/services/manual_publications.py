from __future__ import annotations

import uuid

from django.db import transaction
from django.utils.translation import gettext as _

from catalog.integrations.hal_assistant import readiness_for
from catalog.models import AuditEvent, Publication
from catalog.services.matching import PublicationMatch, normalized, score_publication_matches

PUBLICATION_TYPE_BY_HAL = {
    "ART": "journal_article",
    "COMM": "conference_paper",
    "COUV": "book_chapter",
    "OUV": "book",
    "DOUV": "edited_book",
}


def find_manual_publication_matches(data: dict, limit: int = 5) -> list[PublicationMatch]:
    return score_publication_matches(
        title=normalized(data.get("title", "")),
        authors={normalized(item) for item in data.get("authors", []) if item},
        doi=normalized(data.get("doi", "")),
        publication_year=data.get("publication_year"),
        limit=limit,
    )


@transaction.atomic
def create_manual_draft(*, data: dict, actor, duplicate_reviewed: bool) -> Publication:
    matches = find_manual_publication_matches(data)
    if any(match.score >= 90 for match in matches):
        raise ValueError(
            _(
                "Une notice correspondante très probable existe déjà. "
                "Ouvrez-la au lieu de créer un doublon."
            )
        )
    if matches and not duplicate_reviewed:
        raise ValueError(_("Les rapprochements possibles doivent être examinés."))

    hal_type = data["hal_document_type"]
    publication_type = PUBLICATION_TYPE_BY_HAL[hal_type]
    record = {
        **data,
        "publication_type": publication_type,
        "document_type": hal_type,
        "year": data["publication_year"],
    }
    ready, missing, resolved_type = readiness_for(record)
    if not ready:
        raise ValueError(
            _("Des champs HAL requis sont encore manquants : %(fields)s")
            % {"fields": ", ".join(missing)}
        )

    publication = Publication.objects.create(
        publication_key=f"manual-{uuid.uuid4().hex[:16]}",
        publication_type=publication_type,
        hal_document_type=resolved_type,
        title=data["title"].strip(),
        authors=data["authors"],
        publication_year=data["publication_year"],
        language=data["language"],
        doi=data.get("doi", ""),
        journal_title=data.get("journal_title", ""),
        journal_hal_id=data.get("journal_hal_id", ""),
        book_title=data.get("book_title", ""),
        issn=[data["journal_issn"]] if data.get("journal_issn") else [],
        publisher=data.get("journal_publisher", ""),
        conference_title=data.get("conference_title", ""),
        conference_start_date=data.get("conference_start_date"),
        conference_end_date=data.get("conference_end_date"),
        conference_city=data.get("conference_city", ""),
        conference_country=data.get("conference_country", ""),
        review_state=Publication.ReviewState.DRAFT,
        readiness_state=Publication.ReadinessState.HAL_READY,
        missing_required_fields=[],
    )
    AuditEvent.objects.create(
        actor=actor,
        action="publication.manual_created",
        object_type="publication",
        object_id=str(publication.id),
        metadata={
            "publication_key": publication.publication_key,
            "hal_document_type": publication.hal_document_type,
            "duplicate_candidates_reviewed": bool(matches),
        },
    )
    return publication
