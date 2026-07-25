# HAL Publication Manager

An auditable, read-only-first Django application for managing publication
metadata while reusing the safety-focused
[`hal-assistant`](https://github.com/jfix/hal-assistant) package.

The current milestone imports immutable reviewed-workbook snapshots and exposes
authenticated publication list and detail pages. It deliberately contains no
HAL submission, update, or live reconciliation route.

## Local development

Requirements: Python 3.12+ and `uv`. Node and Docker are not needed to run the
application.

```bash
uv sync --all-groups
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Local development uses `db.sqlite3` and stores immutable snapshots under
`var/media/`. Neither path is committed.

This is a complete local installation: authentication, imports, the database,
static files, and source snapshots all work without PostgreSQL, R2, Node, or a
Cloudflare account.

Import a reviewed workbook in two explicit phases:

```bash
uv run python manage.py import_snapshot path/to/review.xlsx --dry-run
uv run python manage.py import_snapshot path/to/review.xlsx \
  --apply --expected-report-sha256 <checksum>
```

The apply command refuses a report checksum that differs from the new dry run.
Reapplying the same source checksum is an idempotent no-op.

### Portable Docker installation

Someone who does not want to install Python can run the same local application
with Docker:

```bash
docker compose up --build
docker compose exec web python manage.py createsuperuser
```

The bind-mounted `var/` directory contains the SQLite database and immutable
snapshots, so recreating the container does not remove local data.

## Verification

```bash
uv run ruff check .
uv run pytest
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
npm test
```

## Optional Cloudflare deployment shape

```text
Browser
  -> minimal Cloudflare Worker
       -> Django Docker container
            -> managed PostgreSQL
            -> R2 immutable snapshots
            -> pinned hal-assistant package
```

Cloudflare secrets are configured interactively, never committed:

```bash
npx wrangler secret put DJANGO_SECRET_KEY
npx wrangler secret put DATABASE_URL
npx wrangler secret put R2_ACCESS_KEY_ID
npx wrangler secret put R2_SECRET_ACCESS_KEY
npx wrangler secret put R2_ENDPOINT_URL
```

Set the non-secret R2 bucket name and final allowed hosts in `wrangler.jsonc`
before deployment. Run database migrations as a controlled release step before
routing traffic to a new version.

See [ADR 0001](docs/adr/0001-cloudflare-ready-django.md) and the imported
handover documents under `docs/`. The durable
[architecture and implementation plan](docs/IMPLEMENTATION_PLAN.md) defines the
package boundary and the exact read-only milestone.
