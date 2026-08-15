from __future__ import annotations

from unittest.mock import patch

from django.core.cache import cache

from catalog.services.hal_references import search_hal_references


def setup_function() -> None:
    cache.clear()


@patch("catalog.services.hal_references._get_json")
def test_journal_results_are_cached_and_humanities_rank_first(get_json) -> None:
    get_json.side_effect = [
        {
            "response": {
                "docs": [
                    {"docid": 2, "title_s": "Revue générale"},
                    {
                        "docid": 1,
                        "title_s": "Revue de théâtre",
                        "issn_s": ["1234-5678"],
                        "publisher_s": ["Éditions Exemple"],
                    },
                ]
            }
        },
        {"response": {"docs": [{"journalTitle_s": "Revue de théâtre"}]}},
    ]

    first = search_hal_references("journal", "revue")
    second = search_hal_references("journal", "revue")

    assert [item.value for item in first] == ["Revue de théâtre", "Revue générale"]
    assert first[0].hal_id == "1"
    assert first[0].issn == "1234-5678"
    assert first[0].publisher == "Éditions Exemple"
    assert second == first
    assert get_json.call_count == 2


@patch("catalog.services.hal_references._get_json")
def test_book_lookup_is_not_limited_to_humanities(get_json) -> None:
    get_json.return_value = {
        "response": {
            "docs": [
                {
                    "bookTitle_s": "Computing Handbook",
                    "level0_domain_s": ["info"],
                },
                {
                    "bookTitle_s": "Théâtre européen",
                    "level0_domain_s": ["shs"],
                },
            ]
        }
    }

    results = search_hal_references("book", "the")

    assert [item.value for item in results] == [
        "Théâtre européen",
        "Computing Handbook",
    ]
    params = get_json.call_args.args[1]
    assert ("fq", "level0_domain_s:shs") not in params
    assert ("fq", "docType_s:COUV") in params


@patch("catalog.services.hal_references._get_json", side_effect=OSError("offline"))
def test_failed_lookup_is_not_cached(get_json) -> None:
    assert search_hal_references("book", "ouvrage") == []
    assert search_hal_references("book", "ouvrage") == []
    assert get_json.call_count == 2


@patch("catalog.services.hal_references._get_json")
def test_short_or_unknown_lookup_does_not_call_hal(get_json) -> None:
    assert search_hal_references("journal", "x") == []
    assert search_hal_references("unknown", "revue") == []
    get_json.assert_not_called()


@patch("catalog.services.hal_references._get_json")
def test_author_lookup_returns_preferred_hal_forms(get_json) -> None:
    get_json.return_value = {
        "response": {
            "docs": [
                {
                    "docid": "1-42",
                    "fullName_s": "Florence Fix",
                    "idHal_s": "florence-fix",
                    "valid_s": "PREFERRED",
                }
            ]
        }
    }

    results = search_hal_references("author", "Florence")

    assert results[0].value == "Florence Fix"
    assert results[0].hal_id == "florence-fix"
    assert ("fq", "valid_s:PREFERRED") in get_json.call_args.args[1]
