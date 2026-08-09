from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from django.db import transaction

from catalog.models import AuditEvent, DocumentPublicationLink, DocumentSummaryCache, Publication


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


@dataclass(frozen=True)
class PublicationMatch:
    publication: Publication
    score: int
    reasons: tuple[str, ...]


def find_publication_matches(
    summary: DocumentSummaryCache, limit: int = 5
) -> list[PublicationMatch]:
    title = _normalized(summary.document_title)
    authors = {_normalized(item) for item in summary.suggested_authors if item}
    doi = _normalized(summary.suggested_doi)
    matches = []
    for publication in Publication.objects.all().iterator():
        score, reasons = 0, []
        candidate_title = _normalized(publication.title)
        ratio = (
            SequenceMatcher(None, title, candidate_title).ratio()
            if title and candidate_title
            else 0
        )
        if title == candidate_title and title:
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
            a in c or c in a for a in authors for c in candidate_authors
        ):
            score += 20
            reasons.append("auteur en commun")
        if (
            summary.suggested_publication_year
            and publication.publication_year == summary.suggested_publication_year
        ):
            score += 10
            reasons.append("même année")
        if doi and doi == _normalized(publication.doi):
            score += 100
            reasons.append("même DOI")
        if score >= 30:
            matches.append(PublicationMatch(publication, score, tuple(reasons)))
    return sorted(matches, key=lambda item: (-item.score, item.publication.title))[:limit]


@transaction.atomic
def link_summary(*, summary: DocumentSummaryCache, publication: Publication, actor, action: str):
    link = DocumentPublicationLink.objects.create(
        summary=summary, publication=publication, actor=actor, action=action
    )
    AuditEvent.objects.create(
        actor=actor,
        action=f"document.{action}",
        object_type="DocumentSummaryCache",
        object_id=str(summary.id),
        metadata={"publication_key": publication.publication_key},
    )
    return link


@transaction.atomic
def create_draft_from_summary(*, summary: DocumentSummaryCache, actor):
    existing = find_publication_matches(summary)
    if any(match.score >= 90 for match in existing):
        raise ValueError(
            "Une notice correspondante très probable existe déjà ; associez-la au document."
        )
    key = f"document-{summary.document_sha256[:16]}"
    publication, created = Publication.objects.get_or_create(
        publication_key=key,
        defaults={
            "title": summary.document_title,
            "authors": summary.suggested_authors,
            "publication_year": summary.suggested_publication_year,
            "publication_type": summary.suggested_publication_type or "other",
            "doi": summary.suggested_doi,
            "review_state": Publication.ReviewState.DRAFT,
            "readiness_state": Publication.ReadinessState.NEEDS_REVIEW,
        },
    )
    return link_summary(
        summary=summary,
        publication=publication,
        actor=actor,
        action=(
            DocumentPublicationLink.Action.CREATED
            if created
            else DocumentPublicationLink.Action.LINKED
        ),
    )
