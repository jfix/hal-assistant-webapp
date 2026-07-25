# HAL Publication Manager agent guidance

These instructions apply to all work in this repository.

## Mission and priorities

Build a safe, auditable web application for managing publication metadata while
reusing the proven `hal-assistant` package. The application should become more
general over time, but Florence Fix's corpus remains the regression baseline.

Priority order:

1. protect existing HAL records and immutable submission history;
2. preserve original citations, source snapshots, and human decisions;
3. keep imports idempotent and reviewable;
4. improve review ergonomics without duplicating domain rules;
5. generalize only when existing safety behavior remains characterized.

## Non-negotiable safety rules

- Never submit to or update HAL unless a task explicitly authorizes that exact
  operation and environment.
- Never create a HAL record when an accepted or sufficiently matching record
  already exists.
- Treat duplicate detection as a multi-field decision. Title-only similarity is
  evidence, never a final decision.
- Never invent metadata or complete partial conference dates.
- Preserve `raw_citation`/`original_citation` byte-for-byte as source evidence.
- Keep authors, editors, translators, and reviewers as distinct roles.
- New deposits and updates are separate operations in data, UI, audit, and
  confirmation flows.
- Never store credentials, tokens, cookies, authorization headers, or temporary
  credential paths in source control, fixtures, logs, or database snapshots.

## Architecture

- Parsing, normalization, matching, readiness, XML generation, and SWORD safety
  logic remain in the reusable `hal-assistant` package.
- Web views call application services and the package adapter; they do not
  reimplement bibliographic rules.
- SQLite is supported for local development. Cloud deployments use PostgreSQL
  through `DATABASE_URL`.
- Immutable source files use Django storage: local files in development and R2
  through its S3-compatible endpoint in Cloudflare deployments.
- Cloudflare deployment uses a standard Docker image behind a deliberately thin
  JavaScript Worker. All application behavior remains in Django.
- Migrations must be additive and reversible. Never discard evidence, accepted
  HAL IDs, or audit history.

## Verification

Run:

```bash
uv run ruff check .
uv run pytest
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
npm test
```

Do not run HAL network-write commands as part of tests or local development.
