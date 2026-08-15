from __future__ import annotations

import os
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from catalog.models import AuditEvent, DocumentSummaryCache


def cache_retention_days() -> int:
    try:
        value = int(os.getenv("SUMMARY_CACHE_RETENTION_DAYS", "90"))
    except ValueError:
        return 90
    return max(1, value)


@transaction.atomic
def delete_summary_cache_entry(*, entry: DocumentSummaryCache, actor, reason: str) -> None:
    """Delete generated content while preserving a content-free audit record."""
    if hasattr(entry, "publication_link"):
        raise ValueError(_("Un résultat associé à une notice ne peut pas être supprimé."))
    AuditEvent.objects.create(
        actor=actor,
        action="document_summary_cache.deleted",
        object_type="DocumentSummaryCache",
        object_id=str(entry.id),
        before_checksum=entry.document_sha256,
        metadata={
            "owner_id": entry.owner_id,
            "model_name": entry.model_name,
            "generator_version": entry.generator_version,
            "reason": reason,
        },
    )
    source_file = entry.source_file
    entry.delete()
    if source_file:
        source_file.delete(save=False)


def purge_expired_summary_cache(*, apply: bool = True) -> int:
    cutoff = timezone.now() - timedelta(days=cache_retention_days())
    expired = DocumentSummaryCache.objects.filter(
        created_at__lt=cutoff, publication_link__isnull=True
    ).order_by("created_at")
    count = expired.count()
    if apply:
        for entry in expired.iterator():
            delete_summary_cache_entry(entry=entry, actor=None, reason="retention")
    return count
