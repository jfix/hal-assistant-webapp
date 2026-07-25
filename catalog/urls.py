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
]
