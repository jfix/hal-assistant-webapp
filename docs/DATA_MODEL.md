> Imported from jfix/hal-assistant `agent/web-app-handover` at `3ef821f` on 2026-07-25. This is the target model; milestone-one implementation intentionally selects the read-only subset documented in the architecture ADR.

# Proposed web application data model

## Design principles

- A publication is the stable scholarly object; imports, HAL snapshots and
  submissions are observations or operations around it.
- The original citation and imported source rows are immutable.
- Current metadata is editable, but every change is versioned and attributable.
- People have explicit roles. Author, editor and reviewer are not interchangeable.
- Evidence belongs to individual field values, not only to a publication.
- New HAL deposits and updates of known HAL records use separate operations.
- Ledgers and payload checksums are append-only.

Use UUID primary keys internally. Keep the existing deterministic
`publication_id` as a unique external/business key so current JSON, spreadsheets,
fixtures and ledgers remain reconcilable.

## Core entities

### `publication`

| Field | Notes |
| --- | --- |
| `id` | UUID primary key |
| `publication_key` | Existing stable `pub-…` identifier, unique |
| `publication_type` | `book`, `edited_book`, `journal_issue`, `book_chapter`, `dictionary_entry`, `conference_paper`, `journal_article`, `unknown` |
| `hal_document_type` | `OUV`, `COUV`, `COMM`, `ART`, etc. |
| `title` | Current reviewed contribution/item title |
| `language` | BCP 47 or controlled ISO code; default `fr` only when sourced |
| `publication_year` | Four-digit year |
| `pages` | Prefer structured start/end when possible; retain display value |
| `review_state` | draft, needs_review, approved, blocked |
| `readiness_state` | parsed, needs_enrichment, needs_review, hal_ready, preprod_validated, production_submitted |
| `created_at`, `updated_at` | Operational timestamps |
| `version` | Optimistic concurrency counter |

Do not store a mutable `raw_citation` directly here. Link immutable citations
through `source_record`.

### `source_import`

One immutable ingestion event.

Important fields: `id`, `source_type` (`docx`, `xlsx`, `google_sheet`, `hal_api`,
`manual`), source name/URL, retrieved time, file checksum, parser version, import
status, record count and import report checksum.

### `source_record`

The exact source row or paragraph behind a publication.

Important fields: `source_import_id`, `publication_id`, external row/paragraph
locator, immutable original citation, raw JSON snapshot and raw-record checksum.
Multiple source records may point to the same publication.

### `person`

Fields: canonical display name, given/family names, IdHAL, ORCID and optional
normalized matching key. Do not merge people automatically on name alone.

### `publication_contributor`

Join table with publication, person, explicit `role` (`author`, `editor`,
`translator`, etc.), position and source/review state. The unique key includes
publication, person, role and position as appropriate.

### `container`

Represents journals, books, proceedings, series and journal issues.

Fields include container type, title, subtitle/thematic title, volume, issue,
publisher, publisher place, ISBN and ISSN. Use a parent relationship when an
issue belongs to a journal or a contribution belongs to a book/proceedings.

For `Le Paon d’Héra`, the journal is the parent container; issue number and
thematic title belong to an issue container.

### `publisher`

Canonical publisher name plus aliases. Publisher place is modeled through a
separate publication/container association because imprints and cities may vary
over time.

### `event`

Conference/event title, start date, end date, city, country and date precision.
Keep `date_precision`/completeness explicit so year- or month-only evidence can be
displayed without satisfying HAL submission requirements.

### `identifier`

Polymorphic identifier table for DOI, ISBN, ISSN, HAL ID, IdHAL, ORCID and source
system IDs. Store a normalized value, display value, target entity and uniqueness
scope. Validate check digits/formats where applicable without treating format
validity as proof of bibliographic correctness.

## Evidence and review

### `field_assertion`

Represents one proposed, accepted, superseded or rejected value for one field.

Suggested fields:

- entity type and entity ID;
- field path;
- typed value JSON plus normalized comparison value;
- origin (`parser`, `hal`, `crossref`, `openalex`, `publisher`, `fabula`, `human`);
- source evidence ID;
- confidence;
- state (`proposed`, `accepted`, `rejected`, `superseded`);
- created time, reviewer and decision note.

The current publication view can be materialized from accepted assertions, while
the complete history remains available.

### `evidence_source`

Source URL/name, retrieval time, source type, content checksum, optional archived
snapshot reference and notes. Search-result snippets must not be stored as the
sole authority for accepted metadata.

### `review_decision`

Append-only decision containing reviewer, timestamp, scope, previous/proposed
values, outcome and rationale. Use it for field edits, duplicate decisions,
record readiness and production approval.

## HAL reconciliation

### `hal_record`

Known HAL ID, canonical URL, current version, fetched time, raw API snapshot
checksum and selected normalized fields. Keep successive snapshots rather than
overwriting the only copy.

### `hal_candidate_match`

Links a publication to a candidate HAL record with component scores for title,
year, contributors, type, container, issue, pages and identifiers. Store the
algorithm version and final human decision (`same`, `distinct`, `unresolved`).

Any `distinct` override must apply only to that publication/candidate pair and
must include a reviewer and reason.

### `hal_operation`

Represents a `new_deposit` or `update_existing` workflow. Fields include target
environment, target HAL ID for updates, state, publication version, requested by,
approved by, timestamps and failure diagnostics.

### `hal_payload`

Immutable serialized payload with operation ID, content, SHA-256, validation
result, environment and creation time. A production operation must reference the
same payload checksum that passed preproduction.

### `submission_attempt`

Append-only attempt with endpoint/environment, test flag, HTTP status, accepted
flag, returned HAL ID/URL, sanitized response, error and timestamp. A failure for
one payload must not roll back independent successful attempts.

### `submission_batch`

Immutable grouping with manifest checksum, candidate payloads and explicit
exclusions. Batch membership never implies fail-fast behavior.

## Audit and access

### `audit_event`

Append-only actor/action/object event with timestamp, request correlation ID,
before/after checksums and sanitized metadata. Avoid secrets and full
authorization headers.

### `user` and `role_assignment`

Minimum roles:

- `viewer` — read metadata and evidence;
- `reviewer` — edit and accept metadata/duplicate decisions;
- `preprod_submitter` — submit validated payloads to preproduction;
- `production_approver` — approve production batches;
- `production_submitter` — execute an approved production batch;
- `admin` — manage users and configuration, not bypass provenance rules.

## Key constraints

- Unique `publication.publication_key`.
- Unique normalized DOI globally where semantically appropriate.
- A production `update_existing` operation requires a target HAL ID.
- A production payload checksum must equal a successfully preproduction-validated
  checksum.
- Accepted submission attempts and production manifests are immutable.
- An event cannot be HAL-ready for `COMM` unless title, complete start date, city
  and country are present; end date defaults to start date only after explicit
  confirmation that it was a one-day event.
- Optimistic concurrency prevents one reviewer from silently overwriting another.

## Suggested migration order

1. Users/roles and audit scaffolding.
2. Publications, source imports and source records.
3. Contributors, containers, events and identifiers.
4. Assertions, evidence and review decisions.
5. HAL snapshots and candidate matches.
6. Payloads, operations, attempts and immutable batches.
7. Import a frozen reviewed-sheet snapshot in dry-run mode.
8. Reconcile HAL IDs and local ledgers before promoting operational state.

