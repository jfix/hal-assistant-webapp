> Imported from jfix/hal-assistant `agent/web-app-handover` at `3ef821f` on 2026-07-25. The kickoff is retained as historical handover context; this repository and its root `AGENTS.md` are now authoritative.

# Kickoff prompt for the web application task

Copy the prompt below into a new Codex task attached to the selected repository.

---

Build the HAL publication-management web application described in the project
handover.

Before proposing or changing code:

1. read `AGENTS.md` completely;
2. read `docs/WEB_APP_HANDOFF.md`, `docs/DATA_MODEL.md`,
   `docs/WEB_APP_ARCHITECTURE.md` and `docs/TEST_FIXTURES.md`;
3. inspect the existing parsing, enrichment, HAL matching, readiness, XML,
   submission and ledger modules in `src/hal_assistant/`;
4. run the existing test suite and report the baseline;
5. inspect the current Google Sheet only through an immutable export/snapshot;
6. propose an incremental architecture and MVP plan before implementing it.

Primary objective:

Create a secure, auditable web interface and dedicated database for Florence
Fix's publication corpus while reusing the proven HAL Assistant domain logic.
Google Sheets becomes a controlled import/export and collaborative review surface,
not the transactional source of truth.

Non-negotiable behavior:

- never resubmit an accepted or existing HAL record;
- never invent metadata or complete a partial conference date;
- retain original citations and field-level source evidence;
- distinguish authors from editors and new deposits from updates;
- show before/after changes and supporting evidence to a reviewer;
- process publications independently so one failure does not block others;
- use live HAL plus immutable local ledgers for duplicate protection;
- validate the exact immutable XML in preproduction before production;
- show the target environment and exact action before any network submission;
- never store credentials in source control, fixtures, logs or the browser;
- preserve checksums, diagnostics, accepted HAL IDs, audit events and resumability.

For the first task, do not submit or update anything in HAL. Produce:

1. a concise architecture decision record;
2. the initial database schema/migrations;
3. an idempotent dry-run importer for a frozen reviewed-sheet export;
4. characterization tests proving that the existing parser and duplicate-safety
   behavior remain unchanged;
5. a read-only publication list/detail prototype showing original citation,
   structured metadata, evidence, HAL state and readiness.

Stop for a decision only if the repository boundary, hosting/authentication choice
or HAL update mechanism materially changes the implementation. Do not request or
use HAL production credentials during the initial milestone.

---

