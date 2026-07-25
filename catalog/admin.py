from django.contrib import admin

from .models import AuditEvent, FieldAssertion, Publication, SourceImport, SourceRecord


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
