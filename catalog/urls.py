from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
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
    path("publications/", views.publication_list, name="publication-list"),
    path("publications/export/", views.publication_export, name="publication-export"),
    path(
        "publications/<uuid:publication_id>/",
        views.publication_detail,
        name="publication-detail",
    ),
    path(
        "publications/<uuid:publication_id>/xml/",
        views.publication_xml,
        name="publication-xml",
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
