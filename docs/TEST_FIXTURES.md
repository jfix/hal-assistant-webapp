> Imported from jfix/hal-assistant `agent/web-app-handover` at `3ef821f` on 2026-07-25. These cases remain the regression-fixture contract for the web application and reusable package.

# Web application regression fixtures

## Purpose

Turn the difficult real-world cases discovered during the Florence Fix project
into small, sanitized regression fixtures. Fixtures should preserve the relevant
French punctuation and metadata pattern without including credentials, private
tokens or raw HAL response bodies containing unnecessary data.

## Parser and normalization fixtures

1. A citation whose URL ends in four digits that are not the publication year.
   Assert that URL path digits never win over the bibliographic year.
2. French titles with `« guillemets »`, apostrophes, non-breaking space before a
   colon, nested quotations and Roman-numeral centuries.
3. `Avant-propos`, `Introduction` and `Préface` followed by a contextual subtitle.
4. A book chapter with several editors separated by commas and `et`.
5. A record where a person is a volume editor but not a contribution author.
6. A citation containing publisher, city, collection, editors, DOI and page range.
7. Journal title extraction that stops before issue, place and publisher metadata.
8. Container titles that previously accumulated stray bracketed or URL text.

## Publication-type fixtures

- OUV with publisher, city, ISBN and total page count.
- Edited OUV with multiple editors.
- COUV with book title, editors and chapter page range.
- ART with journal title, volume, issue, ISSN and article DOI.
- COMM with complete authoritative event dates.
- COMM with only a year or month hint; assert that it remains blocked.
- A former COMM corrected to COUV; assert that conference requirements disappear
  and the conversion is audited.
- Journal issue with parent journal, issue number and thematic title.

## `Le Paon d’Héra` fixtures

At minimum include:

- issue 1 — `Orphée (1)`;
- issue 2 — `Orphée (2)`;
- issue 3 — `Roméo et Juliette`;
- an issue published by Éditions du Murmure;
- an issue published by Presses universitaires de Franche-Comté;
- journal ISSN `1779-2746`.

Assert that issue number, thematic title, publisher and year remain independent,
and that HAL title-only matching cannot merge distinct issues automatically.

## Duplicate and update fixtures

1. Exact existing HAL record: must be classified as existing and never deposited.
2. Same generic title but different container/year/pages: must require review, not
   be auto-merged.
3. Same scholarly work with punctuation/case differences: should score strongly.
4. Previously accepted local-ledger record absent from an initial HAL query: local
   ledger blocks resubmission.
5. Existing HAL record edited manually after the last local snapshot: update diff
   must use the newly fetched live state.
6. Update operation with known HAL ID: failure must not fall back to new deposit.

## Submission fixtures

- One valid and one invalid record in the same batch; valid processing continues.
- Preproduction acceptance freezes the exact XML checksum.
- Production rejects a payload whose checksum differs from preproduction.
- Resume skips already accepted payloads and retries only pending/rejected ones.
- Existing ledger without `--resume` is never overwritten.
- Production requires explicit execution and confirmation gates.
- Environment label and endpoint are present in the confirmation model.

## Import fixtures

- Reimport of an unchanged snapshot is a no-op.
- Changed sheet row produces field-level proposed assertions.
- Duplicate `publication_id` blocks the import.
- HAL-imported record missing from the original DOCX is created with HAL source
  evidence and preserved HAL ID.
- Conflicting Sheet and live HAL values remain unresolved until human review.
- Original citations remain byte-for-byte unchanged across imports.

## Storage guidance

Keep compact fixtures under `tests/fixtures/` grouped by `parser`, `imports`,
`matching`, `review`, `xml` and `submission`. Use synthetic credentials and mock
HTTP clients. Keep full production archives outside version control and test only
against sanitized minimal extracts.

