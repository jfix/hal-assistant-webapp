from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .models import Publication


def health(request: HttpRequest) -> JsonResponse:
    connection.ensure_connection()
    return JsonResponse({"status": "ok", "database": "reachable"})


@login_required
def home(request: HttpRequest):
    return redirect("publication-list")


@login_required
def publication_list(request: HttpRequest):
    publications = Publication.objects.all()
    query = request.GET.get("q", "").strip()
    publication_type = request.GET.get("type", "").strip()
    readiness = request.GET.get("readiness", "").strip()
    hal_status = request.GET.get("hal_status", "").strip()
    missing_field = request.GET.get("missing", "").strip()

    if query:
        publications = publications.filter(
            Q(title__icontains=query)
            | Q(publication_key__icontains=query)
            | Q(hal_id__icontains=query)
            | Q(authors__icontains=query)
        )
    if publication_type:
        publications = publications.filter(publication_type=publication_type)
    if readiness:
        publications = publications.filter(readiness_state=readiness)
    if hal_status:
        publications = publications.filter(hal_status=hal_status)
    if missing_field:
        publications = publications.filter(
            missing_required_fields__icontains=missing_field
        )

    paginator = Paginator(publications, 25)
    page = paginator.get_page(request.GET.get("page"))
    filter_options = {
        "types": Publication.objects.order_by("publication_type")
        .values_list("publication_type", flat=True)
        .distinct(),
        "readiness": Publication.objects.order_by("readiness_state")
        .values_list("readiness_state", flat=True)
        .distinct(),
        "hal_statuses": Publication.objects.exclude(hal_status="")
        .order_by("hal_status")
        .values_list("hal_status", flat=True)
        .distinct(),
    }
    return render(
        request,
        "catalog/publication_list.html",
        {
            "page": page,
            "filters": {
                "q": query,
                "type": publication_type,
                "readiness": readiness,
                "hal_status": hal_status,
                "missing": missing_field,
            },
            "filter_options": filter_options,
        },
    )


@login_required
def publication_detail(request: HttpRequest, publication_id):
    publication = get_object_or_404(
        Publication.objects.prefetch_related(
            "source_records__source_import",
            "source_records__assertions",
            "assertions__source_record",
        ),
        id=publication_id,
    )
    return render(
        request,
        "catalog/publication_detail.html",
        {"publication": publication},
    )
