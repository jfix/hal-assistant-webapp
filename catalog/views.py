from __future__ import annotations

import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Q
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .integrations.hal_assistant import build_submission_xml
from .models import DocumentPublicationLink, DocumentSummaryCache, FieldAssertion, Publication
from .services.document_matching import (
    create_draft_from_summary,
    find_publication_matches,
    link_summary,
)
from .services.document_summaries import (
    SUMMARY_GENERATOR_VERSION,
    BilingualSummary,
    DocumentSummaryError,
    document_sha256,
    extract_document_text,
    extract_document_title,
    generate_bilingual_summary,
    summary_model,
)
from .services.exports import export_publications_xlsx
from .services.imports import LIST_FIELDS
from .services.review import (
    EDITABLE_FIELDS,
    ReviewConflict,
    ReviewError,
    decide_assertion,
    edit_field,
    pending_proposals,
)
from .services.summary_cache import (
    cache_retention_days,
    delete_summary_cache_entry,
    purge_expired_summary_cache,
)
from .services.summary_limits import SummaryLimitError, generation_slot

REVIEW_PERMISSION = "catalog.review_publication"

# (label, field name, display kind) in detail-page order.
METADATA_FIELDS = (
    ("Titre", "title", "text"),
    ("Année", "publication_year", "year"),
    ("Type HAL", "hal_document_type", "text"),
    ("Auteurs", "authors", "list"),
    ("Directeurs d'ouvrage", "editors", "list"),
    ("Revue", "journal_title", "text"),
    ("Ouvrage / actes", "book_title", "text"),
    ("Volume", "volume", "text"),
    ("Numéro", "issue", "text"),
    ("Pages", "pages", "text"),
    ("Éditeur", "publisher", "text"),
    ("Ville d'édition", "publisher_city", "text"),
    ("DOI", "doi", "doi"),
    ("ISBN", "isbn", "isbn"),
    ("ISSN", "issn", "list"),
    ("Langue", "language", "text"),
    ("URL source", "source_url", "url"),
)
CONFERENCE_FIELDS = (
    ("Événement", "conference_title", "text"),
    ("Début", "conference_start_date", "date"),
    ("Fin", "conference_end_date", "date"),
    ("Ville", "conference_city", "text"),
    ("Pays", "conference_country", "text"),
)


def _edit_text(value) -> str:
    """Render a materialized value as an editable single-line string."""
    if isinstance(value, list | tuple):
        return "; ".join(str(item) for item in value)
    return "" if value is None else str(value)


def _field_descriptors(publication, specs):
    descriptors = []
    for label, name, kind in specs:
        value = getattr(publication, name)
        descriptors.append(
            {
                "label": label,
                "name": name,
                "kind": kind,
                "value": value,
                "edit_value": _edit_text(value),
                "editable": name in EDITABLE_FIELDS,
            }
        )
    return descriptors


def health(request: HttpRequest) -> JsonResponse:
    connection.ensure_connection()
    return JsonResponse({"status": "ok", "database": "reachable"})


@login_required
def home(request: HttpRequest):
    return redirect("document-summary")


@login_required
def document_summary(request: HttpRequest):
    purge_expired_summary_cache()
    result = None
    filename = ""
    cache_hit = False
    entry = None
    if request.method == "POST":
        upload = request.FILES.get("document")
        if upload is None:
            messages.error(request, "Sélectionnez un document PDF ou Word.")
        else:
            filename = upload.name
            try:
                fingerprint = document_sha256(upload)
                model_name = summary_model()
                text = extract_document_text(upload)
                document_title = extract_document_title(upload, text)
                source_filename = upload.name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:255]
                cached = DocumentSummaryCache.objects.filter(
                    owner=request.user,
                    document_sha256=fingerprint,
                    model_name=model_name,
                    generator_version=SUMMARY_GENERATOR_VERSION,
                ).first()
                if cached:
                    DocumentSummaryCache.objects.filter(id=cached.id).update(
                        source_filename=source_filename,
                        document_title=document_title,
                    )
                    cached.source_filename = source_filename
                    cached.document_title = document_title
                    result = BilingualSummary(
                        abstract_en=cached.abstract_en,
                        abstract_fr=cached.abstract_fr,
                        keywords_en=cached.keywords_en,
                        keywords_fr=cached.keywords_fr,
                        suggested_title=cached.document_title,
                        suggested_authors=cached.suggested_authors,
                        suggested_publication_year=cached.suggested_publication_year,
                        suggested_publication_type=cached.suggested_publication_type,
                        suggested_doi=cached.suggested_doi,
                    )
                    entry = cached
                    cache_hit = True
                else:
                    with generation_slot(
                        owner=request.user,
                        document_sha256=fingerprint,
                        model_name=model_name,
                    ):
                        result = generate_bilingual_summary(text)
                    upload.seek(0)
                    entry = DocumentSummaryCache.objects.create(
                        owner=request.user,
                        source_filename=source_filename,
                        document_title=result.suggested_title or document_title,
                        source_file=upload,
                        document_sha256=fingerprint,
                        model_name=model_name,
                        generator_version=SUMMARY_GENERATOR_VERSION,
                        abstract_en=result.abstract_en,
                        abstract_fr=result.abstract_fr,
                        keywords_en=result.keywords_en,
                        keywords_fr=result.keywords_fr,
                        suggested_authors=result.suggested_authors or [],
                        suggested_publication_year=result.suggested_publication_year,
                        suggested_publication_type=result.suggested_publication_type,
                        suggested_doi=result.suggested_doi,
                    )
            except (DocumentSummaryError, SummaryLimitError) as exc:
                messages.error(request, str(exc))
    return render(
        request,
        "catalog/document_summary.html",
        {
            "result": result,
            "filename": filename,
            "cache_hit": cache_hit,
            "entry": entry,
            "matches": find_publication_matches(entry) if entry else [],
            "can_review": request.user.has_perm(REVIEW_PERMISSION),
            "cache_entries": DocumentSummaryCache.objects.filter(owner=request.user)[:20],
            "cache_retention_days": cache_retention_days(),
        },
    )


@login_required
@require_POST
def delete_document_summary_cache(request: HttpRequest, cache_id):
    entry = get_object_or_404(DocumentSummaryCache, id=cache_id, owner=request.user)
    try:
        delete_summary_cache_entry(entry=entry, actor=request.user, reason="owner_request")
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Le résultat mis en cache a été supprimé.")
    return redirect("document-summary")


@login_required
@require_POST
def link_document_summary(request: HttpRequest, cache_id):
    entry = get_object_or_404(DocumentSummaryCache, id=cache_id, owner=request.user)
    if not request.user.has_perm(REVIEW_PERMISSION):
        messages.error(request, "Vous n'avez pas le droit d’associer une notice.")
    elif hasattr(entry, "publication_link"):
        messages.warning(request, "Ce document est déjà associé à une notice.")
    else:
        publication = get_object_or_404(Publication, id=request.POST.get("publication_id"))
        link_summary(
            summary=entry,
            publication=publication,
            actor=request.user,
            action=DocumentPublicationLink.Action.LINKED,
        )
        messages.success(
            request,
            "Le document, les résumés et les mots-clés sont associés à la notice.",
        )
    return redirect("document-summary-cache-detail", cache_id=entry.id)


@login_required
@require_POST
def create_publication_from_document(request: HttpRequest, cache_id):
    entry = get_object_or_404(DocumentSummaryCache, id=cache_id, owner=request.user)
    if not request.user.has_perm(REVIEW_PERMISSION):
        messages.error(request, "Vous n'avez pas le droit de créer un brouillon.")
    elif hasattr(entry, "publication_link"):
        messages.warning(request, "Ce document est déjà associé à une notice.")
    else:
        try:
            link = create_draft_from_summary(summary=entry, actor=request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                "Un brouillon local a été créé. Aucune donnée n’a été envoyée à HAL.",
            )
            return redirect("publication-detail", publication_id=link.publication_id)
    return redirect("document-summary-cache-detail", cache_id=entry.id)


@login_required
def document_summary_cache_detail(request: HttpRequest, cache_id):
    entry = get_object_or_404(DocumentSummaryCache, id=cache_id, owner=request.user)
    result = BilingualSummary(
        abstract_en=entry.abstract_en,
        abstract_fr=entry.abstract_fr,
        keywords_en=entry.keywords_en,
        keywords_fr=entry.keywords_fr,
    )
    return render(
        request,
        "catalog/document_summary_cache_detail.html",
        {
            "entry": entry,
            "result": result,
            "matches": find_publication_matches(entry),
            "can_review": request.user.has_perm(REVIEW_PERMISSION),
        },
    )


@login_required
def associated_document(request: HttpRequest, summary_id):
    """Stream a confirmed source document without exposing its storage URL."""
    entry = get_object_or_404(
        DocumentSummaryCache.objects.filter(publication_link__isnull=False),
        id=summary_id,
    )
    if not entry.source_file:
        raise Http404("Aucun document source n’est disponible.")
    try:
        handle = entry.source_file.open("rb")
    except FileNotFoundError as exc:
        raise Http404("Le document source est introuvable.") from exc
    content_type, _ = mimetypes.guess_type(entry.source_filename)
    response = FileResponse(
        handle,
        as_attachment=False,
        filename=entry.source_filename or "document",
        content_type=content_type or "application/octet-stream",
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


def _filtered_publications(request: HttpRequest):
    """Apply the list filters from the query string to the publication set."""
    publications = Publication.objects.all()
    filters = {
        "q": request.GET.get("q", "").strip(),
        "type": request.GET.get("type", "").strip(),
        "readiness": request.GET.get("readiness", "").strip(),
        "hal_status": request.GET.get("hal_status", "").strip(),
        "missing": request.GET.get("missing", "").strip(),
    }
    if filters["q"]:
        publications = publications.filter(
            Q(title__icontains=filters["q"])
            | Q(publication_key__icontains=filters["q"])
            | Q(hal_id__icontains=filters["q"])
            | Q(authors__icontains=filters["q"])
        )
    if filters["type"]:
        publications = publications.filter(publication_type=filters["type"])
    if filters["readiness"]:
        publications = publications.filter(readiness_state=filters["readiness"])
    if filters["hal_status"]:
        publications = publications.filter(hal_status=filters["hal_status"])
    if filters["missing"]:
        publications = publications.filter(
            missing_required_fields__icontains=filters["missing"]
        )
    return publications, filters


@login_required
def publication_list(request: HttpRequest):
    publications, filters = _filtered_publications(request)
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
            "filters": filters,
            "filter_options": filter_options,
        },
    )


@login_required
def publication_export(request: HttpRequest) -> HttpResponse:
    """Download the currently-filtered corpus as a one-way XLSX snapshot."""
    publications, _ = _filtered_publications(request)
    content = export_publications_xlsx(publications.order_by("publication_key"))
    stamp = timezone.now().strftime("%Y%m%d-%H%M")
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="publications-{stamp}.xlsx"'
    return response


@login_required
def publication_detail(request: HttpRequest, publication_id):
    publication = get_object_or_404(
        Publication.objects.prefetch_related(
            "source_records__source_import",
            "source_records__assertions",
            "assertions__source_record",
            "document_links__summary__owner",
            "document_links__actor",
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
    document_analyses = [
        {
            "link": link,
            "summary": link.summary,
            "can_open_cache": link.summary.owner_id == request.user.id,
        }
        for link in publication.document_links.all()
    ]
    return render(
        request,
        "catalog/publication_detail.html",
        {
            "publication": publication,
            "proposals": proposals,
            "decisions": decisions,
            "document_analyses": document_analyses,
            "can_review": request.user.has_perm(REVIEW_PERMISSION),
            "list_fields": LIST_FIELDS,
            "metadata_fields": _field_descriptors(publication, METADATA_FIELDS),
            "conference_fields": _field_descriptors(publication, CONFERENCE_FIELDS),
        },
    )


@login_required
@require_POST
def edit_field_view(request: HttpRequest, publication_id):
    if not request.user.has_perm(REVIEW_PERMISSION):
        messages.error(request, "Vous n'avez pas le droit de modifier les champs.")
        return redirect("publication-detail", publication_id=publication_id)

    publication = get_object_or_404(Publication, id=publication_id)
    try:
        base_version = int(request.POST.get("base_version", ""))
    except ValueError:
        messages.error(
            request,
            "Jeton de version manquant ou invalide ; rechargez la page et réessayez.",
        )
        return redirect("publication-detail", publication_id=publication_id)

    try:
        decision = edit_field(
            publication=publication,
            field_path=request.POST.get("field", ""),
            actor=request.user,
            edited_value=request.POST.get("edited_value", ""),
            base_version=base_version,
            reason=request.POST.get("reason", ""),
        )
    except ReviewConflict as exc:
        messages.warning(request, str(exc))
    except ReviewError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Champ « {decision.field_path} » modifié.")
    return redirect("publication-detail", publication_id=publication_id)


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
