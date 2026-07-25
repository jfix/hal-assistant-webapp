> Imported from jfix/hal-assistant `agent/web-app-handover` at `3ef821f` on 2026-07-25. The dedicated-repository decision is resolved in favor of this repository; deployment decisions are superseded by `docs/adr/0001-cloudflare-ready-django.md`.

# Web application handover

## Purpose

Build a web application that manages the full publication corpus currently
reviewed in Google Sheets while reusing HAL Assistant's proven parsing,
enrichment, duplicate detection, readiness, XML and submission capabilities.

The application should make bibliographic work understandable to a human
reviewer: every normalized field must remain traceable to the original citation,
an external source or an explicit human decision.

The current reviewed Google Sheet is:

<https://docs.google.com/spreadsheets/d/1SRc0MvSeLRFyaXrMI0twYsZFgb2A3OM_zh2h6cEDMJc/edit>

At the latest handover it contains the consolidated Florence Fix corpus,
including records imported from HAL that were absent from the original Word
document. It should be imported through a repeatable snapshot-and-diff process,
not read as a live transactional database.

## Existing system to reuse

The Python package in `src/hal_assistant/` already provides:

| Capability | Current module |
| --- | --- |
| DOCX section and citation parsing | `parser.py` |
| Stable publication IDs and typed metadata | `models.py` |
| HAL author-corpus search and candidate scoring | `hal.py` |
| Crossref and OpenAlex enrichment | `enrichment.py` |
| Conference review queue and imports | `conference_enrichment.py` |
| Reviewed workbook import | `review_import.py` |
| HAL required-field audit | `hal_requirements.py` |
| AOfr TEI XML generation and validation | `hal_xml.py`, `atomic_xml.py` |
| Preproduction and production SWORD submission | `sword.py` |
| Immutable production batches and checksums | `production.py` |
| Per-record diagnostics | `diagnose_cli.py` |

The web application should call these capabilities through application services.
Do not reimplement them in controllers, React components or database hooks.

## Proven operational facts

- Corrected source document: `A mettre sur HAL-2.docx`.
- Source SHA-256:
  `fc1ad0b67d34e6172a589674b9287cee6daa9d40b79fe0d0566e469ce1b7d176`.
- Author IdHAL: `florence-fix`.
- HAL domain used: `shs.litt`.
- Affiliation structure used for the production run: `95026`.
- The 2026-07-14 run parsed 122 source records and produced an immutable
  64-record production candidate batch. HAL accepted 61 and rejected 3 title-only
  duplicate cases. See `docs/production-run-2026-07-14.md`.
- The corpus later grew by importing Florence Fix's live HAL-authored records into
  the review sheet. The spreadsheet snapshot used to generate the July 2026 Word
  bibliography contained 227 records. Recount during import; do not hard-code it.
- Local output ledgers and production archives remain authoritative for deposits
  made by this tool even if HAL indexing is delayed.

## Important metadata decisions and lessons

- Publication-year extraction must not treat digits at the end of a URL path as
  a year. This is covered by commit `f48b07b` and parser regression tests.
- `Le Paon d’Héra` is a journal (ISSN `1779-2746`). Issue number and thematic title
  are distinct fields. Examples include issue 2 / `Orphée (2)` and issue 3 /
  `Roméo et Juliette`.
- Generic titles such as `Avant-propos`, `Introduction` and `Préface` need their
  contextual subtitle/container for human review and duplicate decisions.
- Michela Landi may be a volume co-editor without being a contribution co-author.
  Author and editor roles must never be merged.
- Conference metadata often exists inside the original citation as proceedings
  title, editors, publisher and place. Event dates require independent evidence.
- Fabula is a strong conference source, supplemented by official institutional
  programmes, publisher pages and journal sites.
- Partial event dates may be displayed as hints, but incomplete conference dates
  block submission. A confirmed one-day event uses identical start/end dates.
- HAL-facing conference cities and countries use French names.
- Publisher and city can vary by issue or imprint. Do not infer them solely from
  another record in the same journal.
- DOI metadata is authoritative for the publication represented by that DOI, but
  an issue-level DOI must not be assigned to a constituent article.
- ISBN applies to print/book-like publications, not conference events as such.
- Existing HAL records may have been edited manually outside this tool. Always
  refetch current HAL metadata before proposing an update.

## Target user workflows

### 1. Import and reconcile

1. Create an immutable import snapshot from DOCX, XLSX/Google Sheet or HAL.
2. Parse without changing operational records.
3. Match by stable IDs and identifiers, then by conservative bibliographic keys.
4. Present created/changed/unchanged/conflicting rows.
5. Require a human decision for conflicts.
6. Apply the accepted diff atomically and record an audit event.

### 2. Review and enrich

- Filter by publication type, readiness, HAL state, confidence and missing field.
- Open a publication detail view containing:
  - original citation;
  - current structured metadata;
  - proposed values and their sources;
  - before/after comparison with per-field accept/reject/edit actions;
  - links for DOI, ISBN, ISSN, external sources and HAL records;
  - a chronological audit trail.
- Allow a reviewer to copy an original value into a proposed field.
- Visually distinguish complete metadata from HAL-submittable metadata.

### 3. Duplicate resolution

- Compare each candidate with live HAL and local accepted ledgers.
- Display title, year, authors, type, container, issue, pages and identifiers side
  by side.
- Record a human outcome: same record, distinct record, or unresolved.
- Any override is scoped to one publication/candidate pair with a reason and
  reviewer identity. Never enable batch-wide duplicate forcing.

### 4. New deposit

1. Run current live duplicate checks.
2. Run required-field and local XML validation.
3. Freeze exact XML plus checksum.
4. Show a human-readable metadata preview and formatted XML.
5. Submit a small preproduction batch independently per record.
6. Freeze only accepted preproduction payloads into a production batch.
7. Require explicit production confirmation showing endpoint and record count.
8. Store every response and accepted HAL link.

### 5. Update existing HAL records

- Refetch the live HAL record immediately before preparing the diff.
- Show original citation, live HAL value and proposed database value.
- Let the reviewer edit and approve fields individually or approve the whole
  record.
- Updates must target the known HAL identifier. They must never create a new
  record as a fallback.
- Preserve both the before snapshot and the submitted after payload.

## Source-of-truth transition

During migration:

1. freeze/export the current Google Sheet;
2. import it as an immutable source snapshot;
3. reconcile it with HAL and local ledgers;
4. obtain human approval for conflicts;
5. promote the accepted database state;
6. thereafter export review views back to Sheets when useful, but do not allow
   untracked two-way edits.

Every later Sheet import must be treated as a proposed changeset with row and
field-level diffs.

## MVP boundary

The first usable release should include:

- authentication for a small trusted reviewer group;
- publication list and detail pages;
- database import of a frozen reviewed-sheet export;
- original citation and evidence display;
- editing with audit history;
- filters for readiness, HAL status and missing fields;
- live HAL duplicate check;
- readiness and XML preview;
- preproduction submission and immutable ledger display;
- explicit, separately gated production submission.

Defer autonomous bulk enrichment, generalized multi-institution tenancy and
automatic production updates until the audited MVP is stable.

## Security and privacy

- Store HAL credentials in a secret manager or injected runtime environment,
  never in the database or browser.
- Do not send SWORD credentials to frontend code.
- Separate read, review, preproduction and production permissions.
- Log metadata decisions and submission actions, but redact authorization headers,
  passwords and session material.
- Provide database backups before enabling production writes.

## Open decisions for the new project

1. Keep the web app in this repository or create a dedicated repository that
   depends on `hal-assistant` as a package?
2. Choose the deployment environment and identity provider.
3. Decide whether enrichment jobs run synchronously for the MVP or through a
   worker queue.
4. Confirm the exact HAL update API/serialization path independently from the
   new-deposit SWORD path before implementing updates.
5. Decide who may approve production submissions and whether two-person approval
   is required.

The recommended repository decision and implementation shape are described in
`docs/WEB_APP_ARCHITECTURE.md`.

