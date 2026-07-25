# Architecture and implementation plan

## Operating model

The application is a Django modular monolith. Its default installation is fully
local:

```text
Browser -> Django -> SQLite
                  -> local immutable snapshot files
                  -> pinned hal-assistant package
```

That installation needs no Cloudflare account, PostgreSQL server, Node runtime,
or network connection after dependencies have been installed. A standard Docker
Compose profile packages the same topology for another computer and persists
all state in its host-mounted `var/` directory.

Cloudflare is an optional production profile:

```text
Browser -> thin Worker -> Django container -> PostgreSQL
                                         -> R2 snapshots
```

The JavaScript Worker is only a container router. It owns no domain logic and
does not make TypeScript or JavaScript part of local application development.

## Package boundary

Keep the following in `hal-assistant`, covered by its existing unit and
regression tests:

- DOCX and reviewed-workbook parsing;
- normalization and stable publication identity;
- HAL search, candidate scoring, and duplicate safeguards;
- Crossref, OpenAlex, and conference enrichment;
- HAL required-field/readiness rules;
- AOfr TEI XML generation and validation;
- SWORD client behavior;
- immutable submission batches, payload checksums, and response ledgers.

Keep the following in this web application:

- accounts, roles, sessions, and permissions;
- materialized publication records;
- immutable source-import and field-assertion persistence;
- import orchestration, dry-run reports, and atomic application;
- reviewer list/detail/filter interfaces;
- audit events and future approval workflow;
- storage and deployment adapters for local files, R2, SQLite, and PostgreSQL.

The adapter in `catalog/integrations/hal_assistant.py` is the explicit boundary.
Views must not import package internals or reproduce bibliographic rules.

## Milestone 1: local read-only corpus

Status: implemented.

Scope:

- authenticated publication list and detail pages;
- readiness, HAL-state, type, missing-field, and text filters;
- original citation and assertion evidence display;
- two-phase reviewed-XLSX import;
- mutation-free dry run with deterministic checksum;
- checksum-gated, atomic apply into immutable source records;
- accepted assertions for new records and non-mutating proposals for changes;
- idempotent handling of an identical snapshot;
- duplicate publication-ID, DOI, and HAL-ID blocking;
- SQLite/local-files default and portable Docker Compose profile;
- production settings for PostgreSQL/R2 and a thin Cloudflare container Worker.

Explicitly excluded:

- live HAL reads;
- Crossref, OpenAlex, or other network enrichment;
- metadata editing or proposal acceptance;
- XML generation in the UI;
- preproduction or production submission;
- any HAL update.

The operator-only import command is part of data initialization, not a web
mutation route. It never contacts HAL.

## Subsequent milestones

### Milestone 2: audited review decisions

- reviewer roles and object-level authorization;
- accept, reject, or edit one proposed field at a time;
- optimistic version checks and append-only decision events;
- before/after displays and chronological audit history;
- export without untracked two-way synchronization.

### Milestone 3: read-only reconciliation and previews

- background enrichment and live HAL candidate reads;
- evidence-backed duplicate comparison;
- HAL readiness and XML preview;
- frozen candidate payloads with no submission capability.

### Milestone 4: separately gated write workflows

This milestone requires a new explicit authorization and safety review. Add
preproduction and production as different permissions and workflows, preserve
every exact payload and response, and never allow an update path to fall back to
a new deposit.

## Deployment acceptance gates

For every release:

1. run the Python tests, lint, Django checks, and migration-drift check;
2. run the Worker syntax and generated-binding checks;
3. build and smoke-test the Docker image on `linux/amd64`;
4. take a database backup before migrations outside development;
5. run migrations as a controlled release step;
6. verify `/healthz`, authentication, and one representative imported record;
7. do not add HAL credentials until a separately authorized write milestone.

The local SQLite installation is the portability baseline. PostgreSQL-specific
behavior must not leak into models or services unless it has a tested SQLite
equivalent.
