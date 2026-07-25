from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("publications/", views.publication_list, name="publication-list"),
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
        "publications/<uuid:publication_id>/assertions/<uuid:assertion_id>/decide/",
        views.decide_assertion_view,
        name="assertion-decide",
    ),
]
