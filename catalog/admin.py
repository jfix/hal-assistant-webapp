from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db.models import Count, Max

from .models import (
    AuditEvent,
    DocumentSummaryCache,
    DocumentSummaryGenerationAttempt,
    FieldAssertion,
    Publication,
    SourceImport,
    SourceRecord,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Publication)
class PublicationAdmin(ReadOnlyAdmin):
    list_display = (
        "publication_key",
        "title",
        "publication_year",
        "publication_type",
        "readiness_state",
        "hal_status",
    )
    list_filter = ("publication_type", "readiness_state", "hal_status")
    search_fields = ("publication_key", "title", "hal_id")


admin.site.register(SourceImport, ReadOnlyAdmin)
admin.site.register(SourceRecord, ReadOnlyAdmin)
admin.site.register(FieldAssertion, ReadOnlyAdmin)
admin.site.register(AuditEvent, ReadOnlyAdmin)
admin.site.register(DocumentSummaryCache, ReadOnlyAdmin)


@admin.register(DocumentSummaryGenerationAttempt)
class DocumentSummaryGenerationAttemptAdmin(ReadOnlyAdmin):
    change_list_template = "admin/catalog/summary_usage_changelist.html"
    list_display = ("owner", "model_name", "created_at", "document_hash_prefix")
    list_filter = ("model_name", "created_at")
    search_fields = ("owner__username", "owner__email", "document_sha256")
    date_hierarchy = "created_at"

    @admin.display(description="Empreinte du document")
    def document_hash_prefix(self, obj) -> str:
        return obj.document_sha256[:12]

    def changelist_view(self, request, extra_context=None):
        users = (
            get_user_model()
            .objects.annotate(
                generation_attempt_count=Count(
                    "document_summary_generation_attempts", distinct=True
                ),
                cached_result_count=Count(
                    "document_summary_cache_entries", distinct=True
                ),
                last_generation_at=Max(
                    "document_summary_generation_attempts__created_at"
                ),
            )
            .order_by("username")
        )
        context = {"summary_usage_users": users, **(extra_context or {})}
        return super().changelist_view(request, extra_context=context)
