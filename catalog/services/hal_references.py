from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass
from urllib.parse import urlencode
from urllib.request import urlopen

from django.core.cache import cache

HAL_JOURNAL_REFERENCE_URL = "https://api.archives-ouvertes.fr/ref/journal/"
HAL_AUTHOR_REFERENCE_URL = "https://api.archives-ouvertes.fr/ref/author/"
HAL_SEARCH_URL = "https://api.archives-ouvertes.fr/search/"
REFERENCE_CACHE_SECONDS = 24 * 60 * 60
REFERENCE_LIMIT = 8

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HALReferenceSuggestion:
    value: str
    source: str
    hal_id: str = ""
    issn: str = ""
    publisher: str = ""
    humanities: bool = False

    def as_dict(self) -> dict[str, str | bool]:
        return asdict(self)


def _normalized(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


def _solr_terms(query: str) -> str:
    terms = _normalized(query).split()[:8]
    return " AND ".join(terms)


def _scalar(value) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


def _get_json(base_url: str, params: list[tuple[str, str]]) -> dict:
    url = f"{base_url}?{urlencode(params)}"
    with urlopen(url, timeout=8) as response:  # noqa: S310 - fixed HAL HTTPS URLs
        return json.load(response)


def _humanities_journal_titles(terms: str) -> set[str]:
    payload = _get_json(
        HAL_SEARCH_URL,
        [
            ("q", f"journalTitle_t:({terms})"),
            ("fq", "docType_s:ART"),
            ("fq", "level0_domain_s:shs"),
            ("fl", "journalTitle_s"),
            ("rows", "40"),
            ("wt", "json"),
        ],
    )
    titles = set()
    for document in payload.get("response", {}).get("docs", []):
        value = document.get("journalTitle_s", "")
        if isinstance(value, list):
            titles.update(_normalized(str(item)) for item in value if item)
        elif value:
            titles.add(_normalized(str(value)))
    return titles


def _journal_suggestions(query: str, terms: str) -> list[HALReferenceSuggestion]:
    payload = _get_json(
        HAL_JOURNAL_REFERENCE_URL,
        [
            ("q", f"title_t:({terms})"),
            ("fq", "valid_s:VALID"),
            ("fl", "docid,title_s,titleAbbr_s,issn_s,eissn_s,publisher_s"),
            ("rows", "20"),
            ("wt", "json"),
        ],
    )
    try:
        humanities_titles = _humanities_journal_titles(terms)
    except Exception:
        logger.warning("hal_humanities_journal_ranking_unavailable", exc_info=True)
        humanities_titles = set()

    suggestions = []
    for document in payload.get("response", {}).get("docs", []):
        title = _scalar(document.get("title_s", ""))
        if not title:
            continue
        suggestions.append(
            HALReferenceSuggestion(
                value=title,
                source="HAL",
                hal_id=str(document.get("docid", "")),
                issn=_scalar(document.get("issn_s") or document.get("eissn_s")),
                publisher=_scalar(document.get("publisher_s", "")),
                humanities=_normalized(title) in humanities_titles,
            )
        )
    normalized_query = _normalized(query)
    return sorted(
        suggestions,
        key=lambda item: (
            not item.humanities,
            not _normalized(item.value).startswith(normalized_query),
            item.value.casefold(),
        ),
    )[:REFERENCE_LIMIT]


def _book_suggestions(query: str, terms: str) -> list[HALReferenceSuggestion]:
    payload = _get_json(
        HAL_SEARCH_URL,
        [
            ("q", f"bookTitle_t:({terms})"),
            ("fq", "docType_s:COUV"),
            ("fl", "bookTitle_s,publisher_s,level0_domain_s"),
            ("rows", "50"),
            ("wt", "json"),
        ],
    )
    seen = set()
    suggestions = []
    for document in payload.get("response", {}).get("docs", []):
        value = document.get("bookTitle_s", "")
        titles = value if isinstance(value, list) else [value]
        publisher_value = document.get("publisher_s", "")
        if isinstance(publisher_value, list):
            publisher_value = publisher_value[0] if publisher_value else ""
        domains = document.get("level0_domain_s", [])
        if isinstance(domains, str):
            domains = [domains]
        humanities = "shs" in {str(domain).casefold() for domain in domains}
        for title_value in titles:
            title = str(title_value).strip()
            key = _normalized(title)
            if not title or key in seen:
                continue
            seen.add(key)
            suggestions.append(
                HALReferenceSuggestion(
                    value=title,
                    source="HAL",
                    publisher=str(publisher_value),
                    humanities=humanities,
                )
            )
    normalized_query = _normalized(query)
    return sorted(
        suggestions,
        key=lambda item: (
            not item.humanities,
            not _normalized(item.value).startswith(normalized_query),
            item.value.casefold(),
        ),
    )[:REFERENCE_LIMIT]


def _author_suggestions(query: str, terms: str) -> list[HALReferenceSuggestion]:
    payload = _get_json(
        HAL_AUTHOR_REFERENCE_URL,
        [
            ("q", f"fullName_t:({terms})"),
            ("fq", "valid_s:PREFERRED"),
            ("fl", "docid,fullName_s,idHal_s"),
            ("rows", "20"),
            ("wt", "json"),
        ],
    )
    normalized_query = _normalized(query)
    seen = set()
    suggestions = []
    for document in payload.get("response", {}).get("docs", []):
        name = _scalar(document.get("fullName_s"))
        key = _normalized(name)
        if not name or key in seen:
            continue
        seen.add(key)
        suggestions.append(
            HALReferenceSuggestion(
                value=name,
                source="HAL",
                hal_id=_scalar(document.get("idHal_s")),
            )
        )
    return sorted(
        suggestions,
        key=lambda item: (
            not _normalized(item.value).startswith(normalized_query),
            item.value.casefold(),
        ),
    )[:REFERENCE_LIMIT]


def search_hal_references(kind: str, query: str) -> list[HALReferenceSuggestion]:
    """Return cached HAL-backed journal or book-title suggestions.

    Humanities results rank first for the current corpus, but other disciplines
    remain searchable so this catalogue can serve broader users later.
    """
    normalized_query = _normalized(query)[:80]
    if kind not in {"journal", "book", "author"} or len(normalized_query) < 2:
        return []
    key = f"hal-reference:v1:{kind}:{normalized_query}"
    cached = cache.get(key)
    if cached is not None:
        return [HALReferenceSuggestion(**item) for item in cached]

    terms = _solr_terms(normalized_query)
    try:
        if kind == "journal":
            suggestions = _journal_suggestions(normalized_query, terms)
        elif kind == "book":
            suggestions = _book_suggestions(normalized_query, terms)
        else:
            suggestions = _author_suggestions(normalized_query, terms)
    except Exception:
        logger.warning("hal_reference_search_unavailable kind=%s", kind, exc_info=True)
        return []
    cache.set(key, [item.as_dict() for item in suggestions], REFERENCE_CACHE_SECONDS)
    return suggestions
