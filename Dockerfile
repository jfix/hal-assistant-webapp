FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    UV_PROJECT_ENVIRONMENT=/opt/venv

RUN apt-get update \
    && apt-get install --no-install-recommends -y git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY manage.py ./
COPY hal_webapp ./hal_webapp
COPY catalog ./catalog
COPY templates ./templates
COPY locale ./locale

RUN chmod -R a+rX . \
    && uv sync --frozen --no-dev --no-editable \
    && DJANGO_DEBUG=0 \
       DJANGO_SECRET_KEY="BuildOnly9xQ2mV7kR4tN8pL5sF1hJ6cD3wE0yU7iO2aB9gH4zX8vK" \
       OPENAI_API_KEY="build-only-placeholder" \
       DJANGO_ALLOWED_HOSTS="build.invalid" \
       DJANGO_CSRF_TRUSTED_ORIGINS="https://build.invalid" \
       python manage.py collectstatic --noinput \
    && adduser --disabled-password --gecos "" app \
    && mkdir -p /app/var/media \
    && chown -R app:app /app/var

USER app
EXPOSE 8080

# Fail closed: apply migrations before gunicorn ever binds the port. If
# migrate fails, this shell exits non-zero, gunicorn never starts, the port
# never opens, and Cloudflare's own startup-timeout/retry path
# (waitForPort -> onError) refuses to bring the container up — instead of
# silently serving traffic against a stale schema. Safe because
# wrangler.jsonc pins max_instances to 1, so there is no concurrent-instance
# migration race. See docs/adr/0001-cloudflare-ready-django.md.
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn hal_webapp.wsgi:application --bind 0.0.0.0:8080 --workers 2 --threads 2 --timeout 120 --access-logfile - --error-logfile -"]
