> Imported from jfix/hal-assistant `main` at `03315f7` on 2026-07-25. Parser implementation and regression ownership remain in the reusable `hal-assistant` package.

# Phase 2: type-specific citation extraction

This phase turns the original Word bibliography into explicit HAL fields before any remote submission.

## Observed source patterns

### ART

Typical pattern:

`« Article title », in Journal title, vol. 37, n°2, 2024, p.1-17.`

The deterministic parser extracts `journal_title` before volume, issue, place, or year metadata.

### COUV

Typical pattern:

`« Chapter title », in Editor Name (éd.), Book title, City, Publisher, 2022, p.5-21.`

The deterministic parser removes the editor prefix and extracts `book_title` before publication-place metadata.

### COMM

The source section labelled “Communication dans un congrès” mostly describes papers later published in proceedings. Typical pattern:

`« Paper title », in Editor Name (éd.), Proceedings title, [Actes du colloque à Institution, City, 2018], Publisher, 2019, p.145-156.`

For HAL `COMM`, the parser extracts:

- `conference_title`: the proceedings/event title before the bracketed event note;
- `conference_city`: event city from the bracketed note;
- `conference_country`: explicit country or a conservative known-city mapping;
- year evidence from the event note, without fabricating an exact start date.

## Extraction principles

1. Preserve `raw_citation` as evidence.
2. Populate only fields supported by deterministic citation text.
3. Record unresolved required fields in the readiness audit.
4. Keep enrichment separate from parsing.
5. Prefer Fabula and official institutional programmes for conference enrichment.
6. Never transform a bare conference year into an invented full date.

