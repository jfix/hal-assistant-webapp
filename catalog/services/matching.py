from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from django.utils.translation import gettext as _

from catalog.models import Publication


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


@dataclass(frozen=True)
class PublicationMatch:
    publication: Publication
    score: int
    reasons: tuple[str, ...]


def score_publication_matches(
    *,
    title: str,
    authors: set[str],
    doi: str,
    publication_year: int | None,
    limit: int = 5,
) -> list[PublicationMatch]:
    """Score every existing publication against normalized candidate fields.

    Shared by document-derived matching (document_matching.py) and
    manually-entered publication matching (manual_publications.py); each
    normalizes its own input shape before calling this.
    """
    matches = []
    for publication in Publication.objects.all().iterator():
        score, reasons = 0, []
        candidate_title = normalized(publication.title)
        ratio = (
            SequenceMatcher(None, title, candidate_title).ratio()
            if title and candidate_title
            else 0
        )
        if title == candidate_title and title:
            score += 70
            reasons.append(_("même titre"))
        elif ratio >= 0.9:
            score += 50
            reasons.append(_("titre très proche (%(ratio)s)") % {"ratio": f"{ratio:.0%}"})
        elif ratio >= 0.75:
            score += 30
            reasons.append(_("titre proche (%(ratio)s)") % {"ratio": f"{ratio:.0%}"})
        candidate_authors = {normalized(str(item)) for item in publication.authors if item}
        if authors and candidate_authors and any(
            a in c or c in a for a in authors for c in candidate_authors
        ):
            score += 20
            reasons.append(_("auteur en commun"))
        if publication_year and publication.publication_year == publication_year:
            score += 10
            reasons.append(_("même année"))
        if doi and doi == normalized(publication.doi):
            score += 100
            reasons.append(_("même DOI"))
        if score >= 30:
            matches.append(PublicationMatch(publication, score, tuple(reasons)))
    return sorted(matches, key=lambda item: (-item.score, item.publication.title))[:limit]
