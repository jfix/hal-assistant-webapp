from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .integrations.hal_assistant import build_submission_xml
from .models import FieldAssertion, Publication
from .services.imports import LIST_FIELDS
from .services.review import (
    ReviewConflict,
    ReviewError,
    decide_assertion,
    pending_proposals,
)

REVIEW_PERMISSION = "catalog.review_publication"


def _edit_text(value) -> str:
    """Render a materialized value as an editable single-line string."""
    if isinstance(value, list | tuple):
        return "; ".join(str(item) for item in value)
    return "" if value is None else str(value)


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
        "readiness": Publication.ReadinessState.choices,
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
    proposals = [
        {
            "assertion": assertion,
            "current": getattr(publication, assertion.field_path, None),
            "proposed_text": _edit_text(assertion.value),
        }
        for assertion in pending_proposals(publication)
    ]
    decisions = publication.decisions.select_related("actor", "assertion")
    return render(
        request,
        "catalog/publication_detail.html",
        {
            "publication": publication,
            "proposals": proposals,
            "decisions": decisions,
            "can_review": request.user.has_perm(REVIEW_PERMISSION),
            "list_fields": LIST_FIELDS,
        },
    )


@login_required
@require_POST
def decide_assertion_view(request: HttpRequest, publication_id, assertion_id):
    if not request.user.has_perm(REVIEW_PERMISSION):
        messages.error(request, "Vous n'avez pas le droit de réviser les modifications.")
        return redirect("publication-detail", publication_id=publication_id)

    assertion = get_object_or_404(
        FieldAssertion,
        id=assertion_id,
        publication_id=publication_id,
    )
    try:
        base_version = int(request.POST.get("base_version", ""))
    except ValueError:
        messages.error(
            request,
            "Jeton de version manquant ou invalide ; rechargez la page et réessayez.",
        )
        return redirect("publication-detail", publication_id=publication_id)

    try:
        outcome = request.POST.get("outcome", "")
        decision = decide_assertion(
            assertion=assertion,
            actor=request.user,
            outcome=outcome,
            base_version=base_version,
            reason=request.POST.get("reason", ""),
            edited_value=(
                request.POST.get("edited_value", "") if outcome == "edited" else None
            ),
        )
    except ReviewConflict as exc:
        messages.warning(request, str(exc))
    except ReviewError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Champ « {decision.field_path} » — {decision.get_outcome_display()}.",
        )
    return redirect("publication-detail", publication_id=publication_id)


@login_required
def publication_xml(request: HttpRequest, publication_id):
    """Debug-only preview of the HAL AOfr submission notice for one record.

    Generated locally by the pinned package from the immutable source row. It
    performs no HAL request and is not a submission route.
    """
    publication = get_object_or_404(
        Publication.objects.prefetch_related("source_records__source_import"),
        id=publication_id,
    )
    source_record = publication.source_records.order_by("-created_at").first()
    submission = (
        build_submission_xml(source_record.raw_data)
        if source_record is not None
        else None
    )
    if request.GET.get("format") == "raw" and submission and submission.xml:
        return HttpResponse(
            submission.xml,
            content_type="application/xml; charset=utf-8",
        )
    return render(
        request,
        "catalog/publication_xml.html",
        {
            "publication": publication,
            "submission": submission,
            "source_record": source_record,
        },
    )
