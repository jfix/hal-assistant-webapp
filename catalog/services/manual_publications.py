from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher

from django.db import transaction

from catalog.integrations.hal_assistant import readiness_for
from catalog.models import AuditEvent, Publication

PUBLICATION_TYPE_BY_HAL = {
    "ART": "journal_article",
    "COMM": "conference_paper",
    "COUV": "book_chapter",
    "OUV": "book",
    "DOUV": "edited_book",
}


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


@dataclass(frozen=True)
class ManualPublicationMatch:
    publication: Publication
    score: int
    reasons: tuple[str, ...]


def find_manual_publication_matches(data: dict, limit: int = 5):
    title = _normalized(data.get("title", ""))
    authors = {_normalized(item) for item in data.get("authors", []) if item}
    doi = _normalized(data.get("doi", ""))
    matches = []
    for publication in Publication.objects.all().iterator():
        score, reasons = 0, []
        candidate_title = _normalized(publication.title)
        ratio = SequenceMatcher(None, title, candidate_title).ratio()
        if title and title == candidate_title:
            score += 70
            reasons.append("même titre")
        elif ratio >= 0.9:
            score += 50
            reasons.append(f"titre très proche ({ratio:.0%})")
        elif ratio >= 0.75:
            score += 30
            reasons.append(f"titre proche ({ratio:.0%})")
        candidate_authors = {_normalized(str(item)) for item in publication.authors if item}
        if authors and candidate_authors and any(
            author in candidate or candidate in author
            for author in authors
            for candidate in candidate_authors
        ):
            score += 20
            reasons.append("auteur en commun")
        if data.get("publication_year") == publication.publication_year:
            score += 10
            reasons.append("même année")
        if doi and doi == _normalized(publication.doi):
            score += 100
            reasons.append("même DOI")
        if score >= 30:
            matches.append(ManualPublicationMatch(publication, score, tuple(reasons)))
    return sorted(matches, key=lambda item: (-item.score, item.publication.title))[:limit]


@transaction.atomic
def create_manual_draft(*, data: dict, actor, duplicate_reviewed: bool) -> Publication:
    matches = find_manual_publication_matches(data)
    if any(match.score >= 90 for match in matches):
        raise ValueError(
            "Une notice correspondante très probable existe déjà. "
            "Ouvrez-la au lieu de créer un doublon."
        )
    if matches and not duplicate_reviewed:
        raise ValueError("Les rapprochements possibles doivent être examinés.")

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
        raise ValueError("Des champs HAL requis sont encore manquants : " + ", ".join(missing))

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
        book_title=data.get("book_title", ""),
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
