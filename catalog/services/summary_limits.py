from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from catalog.models import (
    ActiveDocumentSummaryGeneration,
    DocumentSummaryGenerationAttempt,
)


class SummaryLimitError(Exception):
    """A generation budget or concurrency limit that is safe to display."""


def _positive_limit(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(1, value)


@contextmanager
def generation_slot(*, owner, document_sha256: str, model_name: str):
    """Reserve one paid generation slot and record the attempt atomically."""
    now = timezone.now()
    minute_limit = _positive_limit("SUMMARY_USER_MINUTE_LIMIT", 3)
    daily_limit = _positive_limit("SUMMARY_USER_DAILY_LIMIT", 20)
    global_limit = _positive_limit("SUMMARY_GLOBAL_DAILY_LIMIT", 100)

    try:
        with transaction.atomic():
            ActiveDocumentSummaryGeneration.objects.filter(
                owner=owner,
                created_at__lt=now - timedelta(minutes=3),
            ).delete()
            ActiveDocumentSummaryGeneration.objects.create(owner=owner)
            attempts = DocumentSummaryGenerationAttempt.objects
            recent_user_attempts = attempts.filter(
                owner=owner,
                created_at__gte=now - timedelta(minutes=1),
            ).count()
            if recent_user_attempts >= minute_limit:
                raise SummaryLimitError(
                    "Trop de générations récentes. Attendez une minute avant de réessayer."
                )
            daily_user_attempts = attempts.filter(
                owner=owner,
                created_at__gte=now - timedelta(days=1),
            ).count()
            if daily_user_attempts >= daily_limit:
                raise SummaryLimitError(
                    "Votre limite quotidienne de générations a été atteinte."
                )
            if attempts.filter(created_at__gte=now - timedelta(days=1)).count() >= global_limit:
                raise SummaryLimitError(
                    "La limite quotidienne de l’application a été atteinte."
                )
            DocumentSummaryGenerationAttempt.objects.create(
                owner=owner,
                document_sha256=document_sha256,
                model_name=model_name,
            )
    except IntegrityError as exc:
        raise SummaryLimitError(
            "Une génération est déjà en cours pour votre compte."
        ) from exc

    try:
        yield
    finally:
        ActiveDocumentSummaryGeneration.objects.filter(owner=owner).delete()
