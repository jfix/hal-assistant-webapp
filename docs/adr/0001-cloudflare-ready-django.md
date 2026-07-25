# ADR 0001: Cloudflare-ready Django modular monolith

- Status: accepted
- Date: 2026-07-25
- Source package: `jfix/hal-assistant@03315f7`
- Handover: `jfix/hal-assistant@3ef821f`

## Decision

Build a local-first Django modular monolith with server-rendered, accessible
pages. The complete application runs with Python, SQLite, and local file storage
on a personal computer. Package the same application as an optional standard
Linux/AMD64 Docker image.

Cloudflare is an additional deployment profile, not an application dependency.
That profile replaces SQLite with PostgreSQL and local snapshot storage with R2.

For Cloudflare, a minimal JavaScript Worker routes requests to one stateless
Django container. The Worker contains no authentication, bibliographic,
database, review, or HAL logic. Static files are served by WhiteNoise in the
first milestone to keep the deployment topology small.

Immutable source snapshots use Django's storage abstraction. Development uses
the local filesystem; Cloudflare uses R2's S3-compatible endpoint. The database
and object storage are the only durable state. The container filesystem is
disposable.

## Rationale

This preserves the tested Python domain implementation while remaining
deployable to Cloudflare Containers. A full Workers-native rewrite would require
revalidating parser, duplicate-safety, readiness, XML, and ledger behavior.

SQLite is sufficient for the local corpus, test suite, and a single-computer
installation. PostgreSQL is required only for Cloudflare because container
filesystems are not durable or shared and the application may later run more
than one instance.

## Milestone-one boundary

The first milestone provides authenticated, read-only publication list/detail
pages and an operator-only, checksum-gated snapshot importer. It performs no
live HAL requests and includes no submission or update routes.

The importer has two phases:

1. dry-run produces a deterministic report and checksum without writes;
2. apply reruns the plan and requires that exact checksum before atomically
   storing the snapshot and database observations.

## Consequences

- Local setup requires only Python and `uv`.
- Docker Compose is an optional second local installation route that persists
  SQLite and source snapshots in a host-mounted `var/` directory.
- Node and Wrangler are needed only to exercise the Cloudflare adapter.
- Cloudflare deployment requires a managed PostgreSQL database and Workers Paid.
- Database migrations are a controlled release step, never an automatic
  multi-instance container startup action.
- Future background enrichment can move to Queues/Workflows without changing
  domain ownership.
