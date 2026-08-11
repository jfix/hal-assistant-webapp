from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("service-worker.js", views.service_worker, name="service-worker"),
    path("account/", views.account_settings, name="account-settings"),
    path("summaries/", views.document_summary, name="document-summary"),
    path(
        "summaries/cache/<uuid:cache_id>/",
        views.document_summary_cache_detail,
        name="document-summary-cache-detail",
    ),
    path(
        "summaries/cache/<uuid:cache_id>/delete/",
        views.delete_document_summary_cache,
        name="document-summary-cache-delete",
    ),
    path(
        "summaries/cache/<uuid:cache_id>/link/",
        views.link_document_summary,
        name="document-summary-link",
    ),
    path(
        "summaries/cache/<uuid:cache_id>/create-publication/",
        views.create_publication_from_document,
        name="document-summary-create-publication",
    ),
    path(
        "summaries/source/<uuid:summary_id>/",
        views.associated_document,
        name="associated-document",
    ),
    path("publications/", views.publication_list, name="publication-list"),
    path(
        "publications/new/",
        views.create_publication_manually,
        name="publication-create-manual",
    ),
    path(
        "publications/search/",
        views.publication_search,
        name="publication-search",
    ),
    path("publications/export/", views.publication_export, name="publication-export"),
    path(
        "publications/<uuid:publication_id>/",
        views.publication_detail,
        name="publication-detail",
    ),
    path(
        "publications/<uuid:publication_id>/generate-from-document/",
        views.generate_publication_fields_from_document,
        name="publication-generate-from-document",
    ),
    path(
        "publications/<uuid:publication_id>/xml/",
        views.publication_xml,
        name="publication-xml",
    ),
    path(
        "publications/<uuid:publication_id>/hal/preprod/prepare/",
        views.prepare_hal_preprod,
        name="hal-preprod-prepare",
    ),
    path(
        "hal/preprod/<uuid:operation_id>/",
        views.hal_preprod_operation,
        name="hal-preprod-operation",
    ),
    path(
        "hal/preprod/<uuid:operation_id>/execute/",
        views.execute_hal_preprod,
        name="hal-preprod-execute",
    ),
    path(
        "publications/<uuid:publication_id>/hal/production/prepare/",
        views.prepare_hal_production,
        name="hal-production-prepare",
    ),
    path(
        "hal/production/<uuid:deposit_id>/",
        views.hal_production_deposit,
        name="hal-production-deposit",
    ),
    path(
        "hal/production/<uuid:deposit_id>/execute/",
        views.execute_hal_production,
        name="hal-production-execute",
    ),
    path(
        "publications/<uuid:publication_id>/edit/",
        views.edit_field_view,
        name="publication-edit-field",
    ),
    path(
        "publications/<uuid:publication_id>/assertions/<uuid:assertion_id>/decide/",
        views.decide_assertion_view,
        name="assertion-decide",
    ),
]
