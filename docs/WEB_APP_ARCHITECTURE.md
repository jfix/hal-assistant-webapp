> Imported from jfix/hal-assistant `agent/web-app-handover` at `3ef821f` on 2026-07-25. Repository placement is now resolved. The current deployment decision is Django in a standard Docker image, deployable locally or through Cloudflare Containers; see `docs/adr/0001-cloudflare-ready-django.md`.

# Recommended web application architecture

## Recommendation

Start the web product in a dedicated repository, tentatively
`hal-publication-manager`, and consume the reusable Python domain code from this
repository as a versioned package. Keep changes needed by both CLI and web in
`hal-assistant`; keep deployment, authentication, database, API and frontend code
in the web repository.

This boundary avoids turning a mature safety-focused CLI into a framework-specific
application while preventing the web app from forking its business rules.

If packaging overhead would delay the MVP, a temporary `web/` workspace in this
repository is acceptable, provided domain logic remains under
`src/hal_assistant/` and can later be extracted without rewriting behavior.

## Suggested MVP shape

A modular monolith is sufficient:

```text
Browser
  -> web frontend
  -> authenticated application API
       -> publication/review services
       -> HAL Assistant domain services
       -> PostgreSQL
       -> bounded external-source clients
       -> background job runner (only for slow enrichment/import work)
```

Recommended components:

- Python API service, naturally integrating the existing package;
- PostgreSQL for relational metadata, assertions, evidence and audit trails;
- a server-rendered or React-based frontend with accessible tables and diff views;
- object storage for immutable source snapshots, generated XML and archived
  response bundles;
- a secret manager/runtime secret injection for HAL credentials;
- a lightweight background worker only when synchronous imports/enrichment become
  too slow.

The exact frontend framework and hosting provider should be selected after the
deployment environment and authentication requirements are known.

## Application modules

- `imports`: DOCX, reviewed-sheet and HAL snapshot ingestion;
- `catalog`: publications, people, containers, identifiers and events;
- `evidence`: sources, assertions and provenance;
- `review`: field diffs, approvals and conflict resolution;
- `hal_reconciliation`: live search, snapshots and duplicate decisions;
- `readiness`: required fields and validation;
- `serialization`: immutable XML payloads and previews;
- `submission`: preproduction/production operations and ledgers;
- `audit`: append-only user and system events;
- `exports`: Google Sheets/XLSX, Word bibliography and operational reports.

Each module should expose application services used by both HTTP handlers and,
where useful, CLI adapters.

## Environment separation

Use explicit environment configuration objects rather than free-form URLs:

- local/test — no real submission;
- HAL preproduction — test/validation only;
- HAL production — separately authorized writes.

The UI must display the active target next to every submission control. Production
should require a fresh server-side authorization and a typed/explicit confirmation
containing record count and payload checksum or batch ID.

## First implementation milestones

1. Package boundary and characterization tests around existing domain behavior.
2. Database schema and dry-run import of a frozen Google Sheet export.
3. Read-only publication list/detail UI with original citation and evidence.
4. Versioned editing and before/after review.
5. Live HAL reconciliation and duplicate decision UI.
6. Readiness report and XML preview.
7. Preproduction submission with immutable payloads and attempt ledger.
8. Production approval/execution only after end-to-end recovery testing.

## Observability and recovery

- Assign correlation IDs to imports, enrichment jobs and submissions.
- Capture timing, source endpoint and sanitized failure classes.
- Maintain database backups and object-store versioning before production writes.
- Make every long-running operation resumable and idempotent.
- Provide an operator page for incomplete imports, failed external calls, rejected
  records and ledger reconciliation.

