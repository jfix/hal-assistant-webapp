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

RUN chmod -R a+rX ./locale \
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

CMD ["gunicorn", "hal_webapp.wsgi:application", "--bind", "0.0.0.0:8080", "--workers", "2", "--threads", "2", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-"]
