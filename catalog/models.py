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
        DRAFT = "draft", "Brouillon"
        NEEDS_REVIEW = "needs_review", "À vérifier"
        APPROVED = "approved", "Approuvé"
        BLOCKED = "blocked", "Bloqué"

    class ReadinessState(models.TextChoices):
        PARSED = "parsed", "Analysé"
        NEEDS_ENRICHMENT = "needs_enrichment", "À enrichir"
        NEEDS_REVIEW = "needs_review", "À vérifier"
        HAL_READY = "hal_ready", "Prêt pour HAL"
        PREPROD_VALIDATED = "preprod_validated", "Validé en préproduction"
        PRODUCTION_SUBMITTED = "production_submitted", "Soumis en production"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publication_key = models.CharField(max_length=80, unique=True)
    publication_type = models.CharField(max_length=40)
    hal_document_type = models.CharField(max_length=20, blank=True)
    title = models.TextField()
    publication_year = models.PositiveSmallIntegerField(null=True, blank=True)
    language = models.CharField(max_length=16, blank=True)
    abstract_en = models.TextField(blank=True)
    abstract_fr = models.TextField(blank=True)
    keywords_en = models.JSONField(default=list)
    keywords_fr = models.JSONField(default=list)
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
    hal_synced_version = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-publication_year", "title", "publication_key"]
        indexes = [
            models.Index(fields=["publication_type"]),
            models.Index(fields=["readiness_state"]),
            models.Index(fields=["hal_status"]),
        ]
        permissions = [
            (
                "review_publication",
                "Peut accepter ou rejeter les modifications de champs proposées",
            ),
            (
                "submit_hal_preprod",
                "Peut valider une nouvelle notice dans HAL préproduction",
            ),
            (
                "submit_hal_production",
                "Peut déposer une nouvelle notice dans HAL production",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def display_hal_document_type(self) -> str:
        from catalog.integrations.hal_assistant import hal_document_type_display

        return hal_document_type_display(
            publication_type=self.publication_type,
            explicit_type=self.hal_document_type,
        )[0]

    @property
    def display_hal_document_type_label(self) -> str:
        from catalog.integrations.hal_assistant import hal_document_type_display

        return hal_document_type_display(
            publication_type=self.publication_type,
            explicit_type=self.hal_document_type,
        )[1]

    @property
    def workflow_statuses(self) -> list[dict[str, str]]:
        """Three orthogonal, user-facing publication workflow dimensions."""
        on_hal = bool(self.hal_id)
        # Existing imports predate an explicit accepted status. Only a newly
        # submitted workspace deposit is known not to be published yet.
        published = on_hal and self.hal_status != "submitted"
        ready = (
            not self.missing_required_fields
            and self.readiness_state
            in {
                self.ReadinessState.HAL_READY,
                self.ReadinessState.PREPROD_VALIDATED,
                self.ReadinessState.PRODUCTION_SUBMITTED,
            }
        )

        statuses = [
            {
                "dimension": "HAL",
                "label": (
                    "Publié sur HAL"
                    if published
                    else "Déposé sur HAL"
                    if on_hal
                    else "Brouillon"
                ),
                "compact_dimension": "HAL",
                "compact_label": "Publié" if published else "Déposé" if on_hal else "Brouillon",
                "tone": "published" if on_hal else "neutral",
            }
        ]
        if on_hal:
            modified_since_hal = (
                self.hal_synced_version is not None
                and self.version > self.hal_synced_version
            )
            if modified_since_hal:
                data_label = (
                    "Prêt pour mise à jour HAL"
                    if ready
                    else "Mise à jour à compléter"
                )
                compact_data_label = "Prêt à mettre à jour" if ready else "À compléter"
            else:
                data_label = "Minimum HAL atteint" if ready else "Minimum HAL incomplet"
                compact_data_label = data_label
            statuses.append(
                {
                    "dimension": "Données",
                    "label": data_label,
                    "compact_dimension": "Données",
                    "compact_label": compact_data_label,
                    "tone": "ready" if ready else "warning",
                }
            )
            if self.hal_synced_version is None:
                sync_label, sync_tone = "À vérifier", "warning"
            elif self.version > self.hal_synced_version:
                sync_label, sync_tone = "Modifié", "warning"
            else:
                sync_label, sync_tone = "À jour", "synced"
        else:
            statuses.append(
                {
                    "dimension": "Données",
                    "label": "Prêt pour HAL" if ready else "À compléter",
                    "compact_dimension": "Données",
                    "compact_label": "Prêt" if ready else "À compléter",
                    "tone": "ready" if ready else "warning",
                }
            )
            sync_label, sync_tone = "Jamais synchronisé", "neutral"

        statuses.append(
            {
                "dimension": "Synchronisation",
                "label": sync_label,
                "compact_dimension": "Sync.",
                "compact_label": "Jamais" if sync_label == "Jamais synchronisé" else sync_label,
                "tone": sync_tone,
            }
        )
        return statuses

    @property
    def attention_status(self) -> dict[str, str] | None:
        if self.review_state == self.ReviewState.BLOCKED:
            return {
                "dimension": "Attention",
                "label": "Bloqué",
                "compact_dimension": "Attention",
                "compact_label": "Bloqué",
                "tone": "blocked",
            }
        if self.review_state == self.ReviewState.NEEDS_REVIEW:
            return {
                "dimension": "Attention",
                "label": "À vérifier",
                "compact_dimension": "Attention",
                "compact_label": "À vérifier",
                "tone": "warning",
            }
        return None


class DocumentSummaryCache(ImmutableModel):
    """Generated analysis and its immutable uploaded source document."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="document_summary_cache_entries",
        null=True,
        blank=True,
    )
    source_filename = models.CharField(max_length=255, blank=True)
    document_title = models.CharField(max_length=500, blank=True)
    source_file = models.FileField(upload_to="document-sources/%Y/%m/", blank=True)
    document_sha256 = models.CharField(max_length=64)
    model_name = models.CharField(max_length=100)
    generator_version = models.CharField(max_length=80)
    abstract_en = models.TextField()
    abstract_fr = models.TextField()
    keywords_en = models.JSONField(default=list)
    keywords_fr = models.JSONField(default=list)
    suggested_authors = models.JSONField(default=list)
    suggested_publication_year = models.PositiveSmallIntegerField(null=True, blank=True)
    suggested_publication_type = models.CharField(max_length=40, blank=True)
    suggested_doi = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "document_sha256", "model_name", "generator_version"],
                name="unique_user_document_summary_generation",
            )
        ]

    def __str__(self) -> str:
        return f"{self.document_sha256[:12]} · {self.model_name}"


class DocumentPublicationLink(ImmutableModel):
    """Append-only human decision associating one analysis with one publication."""

    class Action(models.TextChoices):
        LINKED = "linked", "Notice existante"
        CREATED = "created", "Nouveau brouillon local"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    summary = models.OneToOneField(
        DocumentSummaryCache,
        on_delete=models.PROTECT,
        related_name="publication_link",
    )
    publication = models.ForeignKey(
        Publication,
        on_delete=models.PROTECT,
        related_name="document_links",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="document_publication_links",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.summary_id} → {self.publication_id}"


class DocumentSummaryGenerationAttempt(ImmutableModel):
    """Immutable cost-control record created immediately before an AI request."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="document_summary_generation_attempts",
    )
    document_sha256 = models.CharField(max_length=64)
    model_name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "created_at"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.owner} · {self.model_name} · {self.created_at}"


class ActiveDocumentSummaryGeneration(models.Model):
    """Short-lived per-user mutex preventing simultaneous paid generations."""

    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="active_document_summary_generation",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.owner} · {self.created_at}"


class SourceImport(ImmutableModel):
    class SourceType(models.TextChoices):
        DOCX = "docx", "DOCX"
        XLSX = "xlsx", "XLSX"
        GOOGLE_SHEET = "google_sheet", "Instantané Google Sheet"
        HAL_API = "hal_api", "Instantané API HAL"
        MANUAL = "manual", "Manuel"

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
        PROPOSED = "proposed", "Proposé"
        ACCEPTED = "accepted", "Accepté"
        REJECTED = "rejected", "Rejeté"
        SUPERSEDED = "superseded", "Remplacé"

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
        null=True,
        blank=True,
    )
    document_summary = models.ForeignKey(
        DocumentSummaryCache,
        on_delete=models.PROTECT,
        related_name="field_assertions",
        null=True,
        blank=True,
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
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(source_record__isnull=False, document_summary__isnull=True)
                    | models.Q(source_record__isnull=True, document_summary__isnull=False)
                ),
                name="field_assertion_has_exactly_one_source",
            )
        ]

    def __str__(self) -> str:
        return f"{self.publication.publication_key}.{self.field_path} ({self.state})"


class AssertionDecision(ImmutableModel):
    """An append-only reviewer decision on one proposed field assertion.

    Recorded rather than mutating the immutable ``FieldAssertion``. A proposed
    assertion is "pending" until exactly one decision exists for it.
    """

    class Outcome(models.TextChoices):
        ACCEPTED = "accepted", "Accepté"
        REJECTED = "rejected", "Rejeté"
        EDITED = "edited", "Modifié"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publication = models.ForeignKey(
        Publication,
        on_delete=models.PROTECT,
        related_name="decisions",
    )
    assertion = models.OneToOneField(
        "FieldAssertion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="decision",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="hal_assertion_decisions",
    )
    field_path = models.CharField(max_length=200)
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    previous_value = models.JSONField(null=True, blank=True)
    applied_value = models.JSONField(null=True, blank=True)
    reason = models.TextField(blank=True)
    base_version = models.PositiveIntegerField()
    resulting_version = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["publication", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.field_path}: {self.outcome} (v{self.resulting_version})"


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


class HALRemovalRecord(ImmutableModel):
    """Immutable local acknowledgement that a HAL notice was removed manually."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publication = models.ForeignKey(
        Publication,
        on_delete=models.PROTECT,
        related_name="hal_removal_records",
    )
    former_hal_id = models.CharField(max_length=80)
    former_hal_status = models.CharField(max_length=40, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="hal_removal_records",
    )
    reason = models.TextField()
    confirmation_method = models.CharField(
        max_length=40,
        default="manual_hal_interface",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["publication", "former_hal_id"],
                name="unique_publication_former_hal_id",
            )
        ]

    def __str__(self) -> str:
        return f"{self.publication_id} · anciennement {self.former_hal_id}"


class HALCredential(models.Model):
    """Encrypted, replaceable HAL credentials owned by exactly one user."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hal_credential",
    )
    encrypted_login = models.TextField()
    encrypted_password = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"HAL credentials for user {self.user_id}"


class HALOperation(models.Model):
    """A gated new-deposit workflow. Production is deliberately unsupported."""

    class State(models.TextChoices):
        PREPARED = "prepared", "Prêt à confirmer"
        SUBMITTING = "submitting", "Envoi en cours"
        ACCEPTED = "accepted", "Accepté en préproduction"
        REJECTED = "rejected", "Refusé en préproduction"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publication = models.ForeignKey(
        Publication, on_delete=models.PROTECT, related_name="hal_operations"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_hal_operations",
    )
    publication_version = models.PositiveIntegerField()
    state = models.CharField(max_length=20, choices=State.choices)
    duplicate_check = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["publication", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.publication.publication_key} · preprod · {self.state}"


class HALPayload(ImmutableModel):
    """Exact immutable TEI payload prepared for a preproduction operation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.OneToOneField(
        HALOperation, on_delete=models.PROTECT, related_name="payload"
    )
    environment = models.CharField(max_length=20, default="preprod")
    content = models.TextField()
    sha256 = models.CharField(max_length=64)
    validation_errors = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(environment="preprod"),
                name="hal_payload_preprod_only",
            )
        ]

    def __str__(self) -> str:
        return f"{self.operation_id} · {self.sha256[:12]}"


class HALSubmissionAttempt(ImmutableModel):
    """Append-only sanitized record of one HAL preproduction request."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.ForeignKey(
        HALOperation, on_delete=models.PROTECT, related_name="attempts"
    )
    payload = models.ForeignKey(
        HALPayload, on_delete=models.PROTECT, related_name="attempts"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="hal_submission_attempts",
    )
    environment = models.CharField(max_length=20, default="preprod")
    test_mode = models.BooleanField(default=True)
    endpoint = models.URLField(max_length=500)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    accepted = models.BooleanField(default=False)
    returned_hal_id = models.CharField(max_length=80, blank=True)
    returned_hal_url = models.URLField(max_length=1000, blank=True)
    response_body = models.TextField(blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(environment="preprod", test_mode=True),
                name="hal_attempt_preprod_test_only",
            )
        ]

    def __str__(self) -> str:
        return f"{self.operation_id} · {self.status_code or 'network error'}"


class HALProductionDeposit(models.Model):
    """One explicitly confirmed production attempt from an accepted test payload."""

    class State(models.TextChoices):
        PREPARED = "prepared", "Prêt à confirmer"
        SUBMITTING = "submitting", "Dépôt en cours"
        ACCEPTED = "accepted", "Accepté par HAL"
        REJECTED = "rejected", "Refusé par HAL"
        UNCERTAIN = "uncertain", "Résultat à vérifier"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publication = models.ForeignKey(
        Publication, on_delete=models.PROTECT, related_name="hal_production_deposits"
    )
    preprod_operation = models.OneToOneField(
        HALOperation, on_delete=models.PROTECT, related_name="production_deposit"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_hal_production_deposits",
    )
    publication_version = models.PositiveIntegerField()
    payload_sha256 = models.CharField(max_length=64)
    duplicate_check = models.JSONField(default=dict)
    state = models.CharField(max_length=20, choices=State.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.publication_id} · {self.get_state_display()}"


class HALProductionAttempt(ImmutableModel):
    """Append-only sanitized receipt for a real HAL production POST."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    deposit = models.OneToOneField(
        HALProductionDeposit, on_delete=models.PROTECT, related_name="attempt"
    )
    payload = models.ForeignKey(
        HALPayload, on_delete=models.PROTECT, related_name="production_attempts"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="hal_production_attempts",
    )
    endpoint = models.URLField(max_length=500)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    accepted = models.BooleanField(default=False)
    returned_hal_id = models.CharField(max_length=80, blank=True)
    returned_hal_url = models.URLField(max_length=1000, blank=True)
    response_body = models.TextField(blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.deposit_id} · {self.status_code or 'résultat inconnu'}"
