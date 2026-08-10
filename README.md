# HAL Publication Manager

An auditable, read-only-first Django application for managing publication
metadata while reusing the safety-focused
[`hal-assistant`](https://github.com/jfix/hal-assistant) package.

The current milestone imports immutable reviewed-workbook snapshots and exposes
authenticated publication list and detail pages. Authorized users can run an
explicitly confirmed, immutable-payload HAL preproduction `X-test` for a new
deposit after a live multi-field duplicate check. It deliberately contains no
HAL production-write or update route.

## Local development

Requirements: Python 3.12+ and `uv`. Node and Docker are not needed to run the
application.

```bash
uv sync --all-groups
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

The bilingual summary page accepts PDF and DOCX files. It extracts text in
memory (the uploaded file is not stored) and uses the OpenAI Responses API.
Generated summaries and keywords are cached in the database by document hash,
model, and prompt version, so uploading the same file again does not call the
API. Neither the document bytes nor its extracted text are cached.
Cache entries belong to the user who generated them and are retained for 90
days by default. Users can delete their own results from the summary page; all
manual and retention deletions create content-free audit records.

Inspect retention without changing data, then apply it explicitly:

```bash
uv run python manage.py purge_summary_cache
uv run python manage.py purge_summary_cache --apply
```

```bash
export OPENAI_API_KEY="your-api-key"
# Optional; defaults to the balanced gpt-5.6-terra model:
export OPENAI_MODEL="gpt-5.6-terra"
uv run python manage.py runserver
```

Open <http://127.0.0.1:8000/>, sign in, and drag a document onto the upload
area. Image-only scanned PDFs are rejected for now because they require OCR.

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
npx wrangler secret put OPENAI_API_KEY
npx wrangler secret put HAL_CREDENTIAL_ENCRYPTION_KEY
```

Each user manages their own HAL login and password from the authenticated
“Mon compte” page. Those values are encrypted at rest with
`HAL_CREDENTIAL_ENCRYPTION_KEY`, used only in memory by the server-side
preproduction service, and never returned to the browser or stored in the
submission ledger. The encryption key must be a Fernet-compatible URL-safe
base64 encoding of 32 random bytes and must never be committed.

Production startup fails closed unless `DJANGO_ALLOWED_HOSTS` contains exact
hostnames, `DJANGO_CSRF_TRUSTED_ORIGINS` contains exact HTTPS origins, the
Django secret is at least 50 characters, and the OpenAI key is configured. The
placeholder broad `workers.dev` values in `wrangler.jsonc` must therefore be
replaced with the exact deployment hostname before deployment.

Set the non-secret R2 bucket name and final allowed hosts in `wrangler.jsonc`
before deployment. Run database migrations as a controlled release step before
routing traffic to a new version.

See [ADR 0001](docs/adr/0001-cloudflare-ready-django.md) and the imported
handover documents under `docs/`. The durable
[architecture and implementation plan](docs/IMPLEMENTATION_PLAN.md) defines the
package boundary and the exact read-only milestone.
