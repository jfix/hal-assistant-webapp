from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class ImmutableModel(models.Model):
    class Meta:
        abstract = True

    def save(self, *args: object, **kwargs: object) -> None:
        if not self._state.adding:
            raise ValueError(f"{type(self).__name__} records are immutable")
        super().save(*args, **kwargs)


class Publication(models.Model):
    class ReviewState(models.TextChoices):
        DRAFT = "draft", "Draft"
        NEEDS_REVIEW = "needs_review", "Needs review"
        APPROVED = "approved", "Approved"
        BLOCKED = "blocked", "Blocked"

    class ReadinessState(models.TextChoices):
        PARSED = "parsed", "Parsed"
        NEEDS_ENRICHMENT = "needs_enrichment", "Needs enrichment"
        NEEDS_REVIEW = "needs_review", "Needs review"
        HAL_READY = "hal_ready", "HAL ready"
        PREPROD_VALIDATED = "preprod_validated", "Preproduction validated"
        PRODUCTION_SUBMITTED = "production_submitted", "Production submitted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publication_key = models.CharField(max_length=80, unique=True)
    publication_type = models.CharField(max_length=40)
    hal_document_type = models.CharField(max_length=20, blank=True)
    title = models.TextField()
    publication_year = models.PositiveSmallIntegerField(null=True, blank=True)
    language = models.CharField(max_length=16, blank=True)
    pages = models.CharField(max_length=80, blank=True)
    authors = models.JSONField(default=list)
    editors = models.JSONField(default=list)
    journal_title = models.TextField(blank=True)
    book_title = models.TextField(blank=True)
    publisher = models.TextField(blank=True)
    publisher_city = models.CharField(max_length=200, blank=True)
    volume = models.CharField(max_length=80, blank=True)
    issue = models.CharField(max_length=80, blank=True)
    doi = models.CharField(max_length=255, blank=True)
    isbn = models.JSONField(default=list)
    issn = models.JSONField(default=list)
    conference_title = models.TextField(blank=True)
    conference_start_date = models.DateField(null=True, blank=True)
    conference_end_date = models.DateField(null=True, blank=True)
    conference_city = models.CharField(max_length=200, blank=True)
    conference_country = models.CharField(max_length=200, blank=True)
    source_url = models.URLField(max_length=1000, blank=True)
    review_state = models.CharField(
        max_length=24,
        choices=ReviewState.choices,
        default=ReviewState.DRAFT,
    )
    readiness_state = models.CharField(
        max_length=32,
        choices=ReadinessState.choices,
        default=ReadinessState.PARSED,
    )
    missing_required_fields = models.JSONField(default=list)
    hal_status = models.CharField(max_length=40, blank=True)
    hal_id = models.CharField(max_length=80, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-publication_year", "title", "publication_key"]
        indexes = [
            models.Index(fields=["publication_type"]),
            models.Index(fields=["readiness_state"]),
            models.Index(fields=["hal_status"]),
        ]

    def __str__(self) -> str:
        return self.title


class SourceImport(ImmutableModel):
    class SourceType(models.TextChoices):
        DOCX = "docx", "DOCX"
        XLSX = "xlsx", "XLSX"
        GOOGLE_SHEET = "google_sheet", "Google Sheet snapshot"
        HAL_API = "hal_api", "HAL API snapshot"
        MANUAL = "manual", "Manual"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_type = models.CharField(max_length=24, choices=SourceType.choices)
    source_name = models.CharField(max_length=500)
    stored_file = models.CharField(max_length=1000)
    file_sha256 = models.CharField(max_length=64, unique=True)
    parser_version = models.CharField(max_length=80)
    report_sha256 = models.CharField(max_length=64)
    record_count = models.PositiveIntegerField()
    report = models.JSONField(default=dict)
    retrieved_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-retrieved_at", "-created_at"]

    def __str__(self) -> str:
        return f"{self.source_name} ({self.file_sha256[:12]})"


class SourceRecord(ImmutableModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_import = models.ForeignKey(
        SourceImport,
        on_delete=models.PROTECT,
        related_name="records",
    )
    publication = models.ForeignKey(
        Publication,
        on_delete=models.PROTECT,
        related_name="source_records",
    )
    locator = models.CharField(max_length=200)
    original_citation = models.TextField(blank=True)
    raw_data = models.JSONField(default=dict)
    record_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["source_import__retrieved_at", "locator"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_import", "locator"],
                name="unique_source_import_locator",
            )
        ]

    def __str__(self) -> str:
        return f"{self.source_import.source_name}: {self.locator}"


class FieldAssertion(ImmutableModel):
    class State(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        SUPERSEDED = "superseded", "Superseded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publication = models.ForeignKey(
        Publication,
        on_delete=models.PROTECT,
        related_name="assertions",
    )
    source_record = models.ForeignKey(
        SourceRecord,
        on_delete=models.PROTECT,
        related_name="assertions",
    )
    field_path = models.CharField(max_length=200)
    value = models.JSONField()
    normalized_value = models.TextField(blank=True)
    origin = models.CharField(max_length=40)
    confidence = models.CharField(max_length=20, blank=True)
    state = models.CharField(max_length=20, choices=State.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["field_path", "-created_at"]
        indexes = [
            models.Index(fields=["publication", "field_path", "state"]),
        ]

    def __str__(self) -> str:
        return f"{self.publication.publication_key}.{self.field_path} ({self.state})"


class AuditEvent(ImmutableModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="hal_audit_events",
    )
    action = models.CharField(max_length=100)
    object_type = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100)
    correlation_id = models.UUIDField(default=uuid.uuid4)
    before_checksum = models.CharField(max_length=64, blank=True)
    after_checksum = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.action}: {self.object_type}/{self.object_id}"
