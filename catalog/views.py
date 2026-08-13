from __future__ import annotations

import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import connection, models
from django.db.models import Q
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .forms import HALCredentialForm, ManualPublicationForm
from .integrations.hal_assistant import (
    build_submission_xml,
    hal_document_type_display,
    publication_types_for_hal,
)
from .models import (
    DocumentPublicationLink,
    DocumentSummaryCache,
    FieldAssertion,
    HALCredential,
    HALOperation,
    HALProductionDeposit,
    Publication,
)
from .services.document_matching import (
    create_draft_from_summary,
    find_publication_matches,
    link_summary,
)
from .services.document_summaries import (
    BilingualSummary,
    DocumentSummaryError,
)
from .services.exports import export_publications_xlsx
from .services.hal_credentials import (
    HALCredentialError,
    delete_credentials,
    save_credentials,
    saved_login_for,
)
from .services.hal_reconciliation import HALReconciliationError, mark_removed_from_hal
from .services.hal_submission import (
    HALDuplicateError,
    HALSubmissionError,
    execute_preprod_operation,
    execute_production_deposit,
    prepare_preprod_operation,
    prepare_production_deposit,
)
from .services.imports import LIST_FIELDS
from .services.manual_publications import (
    create_manual_draft,
    find_manual_publication_matches,
)
from .services.publication_documents import (
    get_or_generate_summary,
    propose_generated_fields,
)
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
from .services.summary_limits import SummaryLimitError

REVIEW_PERMISSION = "catalog.review_publication"
PREPROD_PERMISSION = "catalog.submit_hal_preprod"
PRODUCTION_PERMISSION = "catalog.submit_hal_production"

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
SUMMARY_FIELDS = (
    ("Résumé français", "abstract_fr", "multiline"),
    ("Mots-clés français", "keywords_fr", "keywords"),
    ("English abstract", "abstract_en", "multiline"),
    ("English keywords", "keywords_en", "keywords"),
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


def _hal_journey(
    publication,
    operation,
    *,
    can_submit: bool,
    can_submit_production: bool,
    has_credentials: bool,
):
    """Build user-facing milestones from existing workflow records."""
    on_hal = bool(publication.hal_id)
    published = on_hal and publication.hal_status != "submitted"
    metadata_ready = (
        not publication.missing_required_fields
        and publication.readiness_state
        in {
            Publication.ReadinessState.HAL_READY,
            Publication.ReadinessState.PREPROD_VALIDATED,
            Publication.ReadinessState.PRODUCTION_SUBMITTED,
        }
    )
    latest_attempt = operation.attempts.first() if operation else None
    preprod_accepted = bool(operation and operation.state == HALOperation.State.ACCEPTED)

    steps = [
        {
            "title": "Notice créée",
            "state": "complete",
            "date": publication.created_at,
            "description": "La notice est enregistrée dans l’application.",
        },
        {
            "title": "Minimum HAL",
            "state": "complete" if metadata_ready or on_hal else "current",
            "date": publication.updated_at if metadata_ready else None,
            "description": (
                "Les métadonnées minimales demandées par HAL sont présentes."
                if metadata_ready or on_hal
                else (
                    f"{len(publication.missing_required_fields)} champ(s) "
                    "obligatoire(s) à compléter."
                    if publication.missing_required_fields
                    else "La notice doit encore être vérifiée avant le test HAL."
                )
            ),
            "action": "complete_metadata" if not metadata_ready and not on_hal else "",
        },
    ]

    if on_hal and operation is None:
        steps.extend(
            [
                {
                    "title": "Contrôle des doublons",
                    "state": "external",
                    "description": "Étape réalisée hors de cette application.",
                },
                {
                    "title": "Test en préproduction",
                    "state": "external",
                    "description": "Aucun test historique n’est enregistré ici.",
                },
            ]
        )
    else:
        steps.append(
            {
                "title": "Contrôle des doublons",
                "state": "complete" if operation else ("current" if metadata_ready else "future"),
                "date": operation.created_at if operation else None,
                "description": (
                    "Le contrôle multi-champs a été effectué lors de la préparation du test."
                    if operation
                    else "HAL sera interrogé avant de préparer le test."
                ),
                "action": (
                    "prepare_test" if metadata_ready and not operation and can_submit else ""
                ),
            }
        )
        if not operation:
            preprod_state = "future"
            preprod_description = "Cette étape sera débloquée après le contrôle des doublons."
            preprod_action = ""
        elif preprod_accepted:
            preprod_state = "complete"
            preprod_description = "HAL préproduction a accepté la notice de test."
            preprod_action = "view_history"
        elif operation.state == HALOperation.State.PREPARED:
            if not can_submit:
                preprod_state = "blocked"
                preprod_description = "Votre compte n’a pas l’autorisation d’envoyer ce test."
                preprod_action = ""
            else:
                preprod_state = "current" if has_credentials else "blocked"
                preprod_description = (
                    "Le test est prêt à être envoyé avec vos identifiants HAL."
                    if has_credentials
                    else "Ajoutez vos identifiants HAL personnels pour envoyer le test."
                )
                preprod_action = "resume_test" if has_credentials else "configure_credentials"
        elif operation.state == HALOperation.State.SUBMITTING:
            preprod_state = "current"
            preprod_description = "L’envoi du test est en cours."
            preprod_action = "view_history"
        else:
            preprod_state = "blocked"
            preprod_description = (
                "Le dernier test a échoué. Préparez un nouveau contrôle après correction."
            )
            preprod_action = "prepare_test" if can_submit else "view_history"
        steps.append(
            {
                "title": "Test en préproduction",
                "state": preprod_state,
                "date": (
                    latest_attempt.created_at
                    if latest_attempt
                    else operation.created_at
                    if operation
                    else None
                ),
                "description": preprod_description,
                "action": preprod_action,
            }
        )

    steps.append(
        {
            "title": "Publication sur HAL",
            "state": (
                "complete"
                if on_hal
                else "current"
                if preprod_accepted and can_submit_production
                else "blocked"
                if preprod_accepted
                else "future"
            ),
            "date": None,
            "description": (
                (
                    f"La notice est publiée sous l’identifiant {publication.hal_id}."
                    if published
                    else f"Le dépôt {publication.hal_id} a été transmis et attend son statut HAL."
                )
                if on_hal
                else (
                    "Le XML testé peut maintenant être préparé pour le dépôt réel."
                    if can_submit_production
                    else "Une autorisation de dépôt HAL production est requise."
                    if preprod_accepted
                    else "Le dépôt réel sera débloqué après validation en préproduction."
                )
            ),
            "action": (
                "open_hal"
                if on_hal
                else "prepare_production"
                if preprod_accepted and can_submit_production
                else ""
            ),
        }
    )
    completed = sum(step["state"] == "complete" for step in steps)
    return {
        "steps": steps,
        "completed": completed,
        "total": len(steps),
        "published": on_hal,
    }


@login_required
def account_settings(request: HttpRequest):
    saved_login = saved_login_for(request.user)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "delete_hal_credentials":
            delete_credentials(user=request.user)
            messages.success(request, "Vos identifiants HAL ont été supprimés.")
            return redirect("account-settings")
        form = HALCredentialForm(request.POST)
        if form.is_valid():
            try:
                save_credentials(
                    user=request.user,
                    login=form.cleaned_data["login"],
                    password=form.cleaned_data["password"],
                )
            except (HALCredentialError, ValueError) as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Vos identifiants HAL ont été enregistrés.")
                return redirect("account-settings")
    else:
        form = HALCredentialForm(initial={"login": saved_login})
    return render(
        request,
        "catalog/account_settings.html",
        {"form": form, "has_hal_credentials": bool(saved_login)},
    )


def health(request: HttpRequest) -> JsonResponse:
    connection.ensure_connection()
    return JsonResponse({"status": "ok", "database": "reachable"})


@never_cache
@require_GET
def service_worker(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "catalog/service-worker.js",
        content_type="application/javascript; charset=utf-8",
    )


@login_required
def home(request: HttpRequest):
    publications = Publication.objects.all()
    drafts = publications.filter(hal_id="")
    ready_states = {
        Publication.ReadinessState.HAL_READY,
        Publication.ReadinessState.PREPROD_VALIDATED,
        Publication.ReadinessState.PRODUCTION_SUBMITTED,
    }
    return render(
        request,
        "catalog/home.html",
        {
            "drafts": drafts.order_by("-updated_at")[:3],
            "stats": {
                "total": publications.count(),
                "drafts": drafts.count(),
                "ready": drafts.filter(
                    readiness_state__in=ready_states,
                    missing_required_fields=[],
                ).count(),
                "published": publications.exclude(hal_id="").count(),
                "modified": publications.exclude(hal_id="")
                .filter(hal_synced_version__lt=models.F("version"))
                .count(),
            },
        },
    )


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
                generation = get_or_generate_summary(upload=upload, owner=request.user)
                result = generation.result
                entry = generation.entry
                cache_hit = generation.cache_hit
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
        "content_missing": request.GET.get("content_missing", "").strip(),
        "workflow": request.GET.get("workflow", "").strip(),
    }
    if filters["q"]:
        publications = publications.filter(
            Q(title__icontains=filters["q"])
            | Q(publication_key__icontains=filters["q"])
            | Q(hal_id__icontains=filters["q"])
            | Q(authors__icontains=filters["q"])
        )
    if filters["type"]:
        publications = publications.filter(
            Q(hal_document_type__iexact=filters["type"])
            | Q(
                hal_document_type="",
                publication_type__in=publication_types_for_hal(filters["type"]),
            )
        )
    if filters["readiness"]:
        publications = publications.filter(readiness_state=filters["readiness"])
    if filters["hal_status"]:
        publications = publications.filter(hal_status=filters["hal_status"])
    if filters["missing"]:
        publications = publications.filter(
            missing_required_fields__icontains=filters["missing"]
        )
    if filters["content_missing"] == "abstracts":
        publications = publications.filter(Q(abstract_fr="") | Q(abstract_en=""))
    elif filters["content_missing"] == "abstract_fr":
        publications = publications.filter(abstract_fr="")
    elif filters["content_missing"] == "abstract_en":
        publications = publications.filter(abstract_en="")
    elif filters["content_missing"] == "keywords":
        publications = publications.filter(Q(keywords_fr=[]) | Q(keywords_en=[]))
    elif filters["content_missing"] == "keywords_fr":
        publications = publications.filter(keywords_fr=[])
    elif filters["content_missing"] == "keywords_en":
        publications = publications.filter(keywords_en=[])
    elif filters["content_missing"] == "bilingual_content":
        publications = publications.filter(
            Q(abstract_fr="")
            | Q(abstract_en="")
            | Q(keywords_fr=[])
            | Q(keywords_en=[])
        )
    if filters["workflow"] == "draft":
        publications = publications.filter(hal_id="")
    elif filters["workflow"] == "published":
        publications = publications.exclude(hal_id="")
    elif filters["workflow"] == "modified":
        publications = publications.exclude(hal_id="").filter(
            hal_synced_version__lt=models.F("version")
        )
    return publications, filters


@login_required
def publication_list(request: HttpRequest):
    publications, filters = _filtered_publications(request)
    sort_field = request.GET.get("sort", "").strip()
    sort_direction = request.GET.get("direction", "asc").strip()
    sort_definitions = {
        "title": ("title", "publication_key"),
        "year": ("publication_year", "title"),
        "type": ("hal_document_type", "publication_type", "title"),
        "state": ("hal_id", "readiness_state", "title"),
        "hal": ("hal_id", "title"),
    }
    if sort_field in sort_definitions:
        descending = sort_direction == "desc"
        publications = publications.order_by(
            *(
                f"-{field}" if descending else field
                for field in sort_definitions[sort_field]
            )
        )
    else:
        sort_field = ""
        sort_direction = "asc"
    paginator = Paginator(publications, 25)
    page = paginator.get_page(request.GET.get("page"))
    type_codes = {
        str(code).upper()
        for code in Publication.objects.exclude(hal_document_type="").values_list(
            "hal_document_type", flat=True
        )
    }
    for publication_type in Publication.objects.filter(
        hal_document_type=""
    ).values_list("publication_type", flat=True):
        type_codes.add(
            hal_document_type_display(publication_type=publication_type)[0]
        )
    filter_options = {
        "types": [
            {
                "code": code,
                "label": hal_document_type_display(
                    publication_type="", explicit_type=code
                )[1],
            }
            for code in sorted(type_codes)
        ],
        "readiness": Publication.ReadinessState.choices,
        "hal_statuses": Publication.objects.exclude(hal_status="")
        .order_by("hal_status")
        .values_list("hal_status", flat=True)
        .distinct(),
    }
    sort_headers = {}
    for field in sort_definitions:
        active = field == sort_field
        next_direction = "desc" if active and sort_direction == "asc" else "asc"
        params = request.GET.copy()
        params["sort"] = field
        params["direction"] = next_direction
        params.pop("page", None)
        sort_headers[field] = {
            "url": f"?{params.urlencode()}",
            "active": active,
            "indicator": "↑" if active and sort_direction == "asc" else "↓",
            "aria_sort": (
                "ascending"
                if active and sort_direction == "asc"
                else "descending"
                if active
                else "none"
            ),
        }
    return render(
        request,
        "catalog/publication_list.html",
        {
            "page": page,
            "filters": filters,
            "filter_options": filter_options,
            "sort_headers": sort_headers,
            "sort_field": sort_field,
            "sort_direction": sort_direction,
        },
    )


@login_required
@require_GET
def publication_search(request: HttpRequest) -> JsonResponse:
    """Small permission-gated lookup used by the document association typeahead."""
    if not request.user.has_perm(REVIEW_PERMISSION):
        return JsonResponse({"error": "Accès refusé."}, status=403)
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        return JsonResponse({"results": []})
    publications = Publication.objects.filter(
        Q(title__icontains=query)
        | Q(authors__icontains=query)
        | Q(publication_key__icontains=query)
        | Q(hal_id__icontains=query)
    ).order_by("title")[:8]
    return JsonResponse(
        {
            "results": [
                {
                    "id": str(publication.id),
                    "title": publication.title,
                    "authors": [str(author) for author in publication.authors],
                    "year": publication.publication_year,
                    "hal_type": publication.display_hal_document_type,
                    "hal_id": publication.hal_id,
                }
                for publication in publications
            ]
        }
    )


@login_required
def create_publication_manually(request: HttpRequest):
    if not request.user.has_perm(REVIEW_PERMISSION):
        messages.error(request, "Vous n’avez pas le droit de créer un brouillon.")
        return redirect("home")

    form = ManualPublicationForm(request.POST or None)
    matches = []
    duplicate_blocked = False
    if request.method == "POST" and form.is_valid():
        matches = find_manual_publication_matches(form.cleaned_data)
        duplicate_blocked = any(match.score >= 90 for match in matches)
        duplicate_reviewed = request.POST.get("duplicate_reviewed") == "1"
        if not matches or (duplicate_reviewed and not duplicate_blocked):
            try:
                publication = create_manual_draft(
                    data=form.cleaned_data,
                    actor=request.user,
                    duplicate_reviewed=duplicate_reviewed,
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    "Le brouillon local a été créé. Aucune donnée n’a été envoyée à HAL.",
                )
                return redirect("publication-detail", publication_id=publication.id)

    suggestions = {
        "journals": Publication.objects.exclude(journal_title="")
        .order_by("journal_title")
        .values_list("journal_title", flat=True)
        .distinct(),
        "books": Publication.objects.exclude(book_title="")
        .order_by("book_title")
        .values_list("book_title", flat=True)
        .distinct(),
        "conferences": Publication.objects.exclude(conference_title="")
        .order_by("conference_title")
        .values_list("conference_title", flat=True)
        .distinct(),
        "cities": Publication.objects.exclude(conference_city="")
        .order_by("conference_city")
        .values_list("conference_city", flat=True)
        .distinct(),
        "countries": Publication.objects.exclude(conference_country="")
        .order_by("conference_country")
        .values_list("conference_country", flat=True)
        .distinct(),
    }
    return render(
        request,
        "catalog/publication_create_manual.html",
        {
            "form": form,
            "matches": matches,
            "duplicate_blocked": duplicate_blocked,
            "suggestions": suggestions,
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
            "assertions__document_summary",
            "document_links__summary__owner",
            "document_links__actor",
            "hal_operations__attempts",
            "hal_removal_records__actor",
        ),
        id=publication_id,
    )
    proposals = [
        {
            "assertion": assertion,
            "current": getattr(publication, assertion.field_path, None),
            "current_text": _edit_text(getattr(publication, assertion.field_path, None)),
            "proposed_text": _edit_text(assertion.value),
            "label": dict((name, label) for label, name, _kind in SUMMARY_FIELDS).get(
                assertion.field_path, assertion.field_path
            ),
            "source_label": (
                assertion.document_summary.source_filename
                if assertion.document_summary
                else assertion.origin
            ),
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
    latest_hal_operation = publication.hal_operations.first()
    can_submit_preprod = request.user.has_perm(PREPROD_PERMISSION)
    can_submit_production = request.user.has_perm(PRODUCTION_PERMISSION)
    hal_journey = _hal_journey(
        publication,
        latest_hal_operation,
        can_submit=can_submit_preprod,
        can_submit_production=can_submit_production,
        has_credentials=HALCredential.objects.filter(user=request.user).exists(),
    )
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
            "summary_fields": _field_descriptors(publication, SUMMARY_FIELDS),
            "summary_generation_missing": any(
                not getattr(publication, name) for _label, name, _kind in SUMMARY_FIELDS
            ),
            "latest_hal_operation": latest_hal_operation,
            "can_submit_preprod": can_submit_preprod,
            "can_submit_production": can_submit_production,
            "hal_journey": hal_journey,
            "latest_hal_removal": publication.hal_removal_records.first(),
        },
    )


@login_required
@require_POST
def reconcile_hal_removal(request: HttpRequest, publication_id):
    publication = get_object_or_404(Publication, id=publication_id)
    if not request.user.has_perm(REVIEW_PERMISSION):
        messages.error(
            request,
            "Vous n’avez pas le droit de modifier l’état HAL de cette notice.",
        )
        return redirect("publication-detail", publication_id=publication.id)
    try:
        mark_removed_from_hal(
            publication=publication,
            actor=request.user,
            confirmed_hal_id=request.POST.get("confirmed_hal_id", ""),
            reason=request.POST.get("reason", ""),
            remote_removal_confirmed=request.POST.get("remote_removal_confirmed") == "yes",
        )
    except HALReconciliationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            "La notice a été remise en brouillon localement. L’historique HAL est conservé.",
        )
    return redirect("publication-detail", publication_id=publication.id)


@login_required
@require_POST
def generate_publication_fields_from_document(request: HttpRequest, publication_id):
    publication = get_object_or_404(Publication, id=publication_id)
    if not request.user.has_perm(REVIEW_PERMISSION):
        messages.error(
            request,
            "Vous n’avez pas le droit de proposer des résumés et mots-clés.",
        )
        return redirect("publication-detail", publication_id=publication.id)

    upload = request.FILES.get("document")
    if upload is None:
        messages.error(request, "Sélectionnez un document PDF ou Word.")
        return redirect("publication-detail", publication_id=publication.id)

    try:
        generation = get_or_generate_summary(upload=upload, owner=request.user)
        _link, assertions, created = propose_generated_fields(
            publication=publication,
            summary=generation.entry,
            actor=request.user,
        )
    except (DocumentSummaryError, SummaryLimitError, ValueError) as exc:
        messages.error(request, str(exc))
    else:
        if not created:
            messages.info(request, "Ce document a déjà été analysé pour cette notice.")
        elif assertions:
            cache_note = " Le résultat existant a été réutilisé." if generation.cache_hit else ""
            messages.success(
                request,
                f"{len(assertions)} proposition(s) à vérifier ont été créées.{cache_note}",
            )
        else:
            messages.success(
                request,
                "Le document est associé ; les valeurs générées sont identiques à la notice.",
            )
    return redirect("publication-detail", publication_id=publication.id)


@login_required
@require_POST
def prepare_hal_preprod(request: HttpRequest, publication_id):
    if not request.user.has_perm(PREPROD_PERMISSION):
        messages.error(request, "Vous n’avez pas le droit de valider dans HAL préproduction.")
        return redirect("publication-detail", publication_id=publication_id)
    publication = get_object_or_404(Publication, id=publication_id)
    try:
        operation = prepare_preprod_operation(publication=publication, actor=request.user)
    except HALDuplicateError as exc:
        messages.error(request, str(exc))
    except HALSubmissionError as exc:
        messages.error(request, str(exc))
    else:
        return redirect("hal-preprod-operation", operation_id=operation.id)
    return redirect("publication-detail", publication_id=publication_id)


@login_required
def hal_preprod_operation(request: HttpRequest, operation_id):
    operation = get_object_or_404(
        HALOperation.objects.select_related(
            "publication", "payload", "requested_by"
        ).prefetch_related("attempts"),
        id=operation_id,
    )
    return render(
        request,
        "catalog/hal_preprod_operation.html",
        {
            "operation": operation,
            "publication": operation.publication,
            "can_submit_preprod": request.user.has_perm(PREPROD_PERMISSION),
        },
    )


@login_required
@require_POST
def execute_hal_preprod(request: HttpRequest, operation_id):
    operation = get_object_or_404(
        HALOperation.objects.select_related("publication", "payload"), id=operation_id
    )
    if not request.user.has_perm(PREPROD_PERMISSION):
        messages.error(request, "Vous n’avez pas le droit de valider dans HAL préproduction.")
    elif request.POST.get("confirmation", "").strip() != operation.publication.publication_key:
        messages.error(request, "La confirmation ne correspond pas à l’identifiant de la notice.")
    else:
        try:
            attempt = execute_preprod_operation(operation=operation, actor=request.user)
        except (HALDuplicateError, HALSubmissionError) as exc:
            messages.error(request, str(exc))
        else:
            if attempt.accepted:
                messages.success(request, "HAL préproduction a accepté la notice de test.")
            else:
                messages.error(request, "HAL préproduction a refusé la notice de test.")
    return redirect("hal-preprod-operation", operation_id=operation.id)


@login_required
@require_POST
def prepare_hal_production(request: HttpRequest, publication_id):
    publication = get_object_or_404(Publication, id=publication_id)
    if not request.user.has_perm(PRODUCTION_PERMISSION):
        messages.error(request, "Vous n’avez pas le droit de déposer dans HAL production.")
        return redirect("publication-detail", publication_id=publication.id)
    operation = publication.hal_operations.filter(state=HALOperation.State.ACCEPTED).first()
    if operation is None:
        messages.error(request, "Un test de préproduction accepté est requis.")
        return redirect("publication-detail", publication_id=publication.id)
    try:
        deposit = prepare_production_deposit(preprod_operation=operation, actor=request.user)
    except (HALDuplicateError, HALSubmissionError) as exc:
        messages.error(request, str(exc))
        return redirect("publication-detail", publication_id=publication.id)
    return redirect("hal-production-deposit", deposit_id=deposit.id)


@login_required
def hal_production_deposit(request: HttpRequest, deposit_id):
    deposit = get_object_or_404(
        HALProductionDeposit.objects.select_related(
            "publication", "preprod_operation__payload", "requested_by"
        ),
        id=deposit_id,
    )
    return render(
        request,
        "catalog/hal_production_deposit.html",
        {
            "deposit": deposit,
            "publication": deposit.publication,
            "can_submit_production": request.user.has_perm(PRODUCTION_PERMISSION),
        },
    )


@login_required
@require_POST
def execute_hal_production(request: HttpRequest, deposit_id):
    deposit = get_object_or_404(
        HALProductionDeposit.objects.select_related(
            "publication", "preprod_operation__payload"
        ),
        id=deposit_id,
    )
    if not request.user.has_perm(PRODUCTION_PERMISSION):
        messages.error(request, "Vous n’avez pas le droit de déposer dans HAL production.")
    elif request.POST.get("confirmation", "").strip() != "DÉPOSER SUR HAL":
        messages.error(request, "La phrase de confirmation ne correspond pas.")
    elif request.POST.get("understood") != "yes":
        messages.error(request, "Confirmez que ce dépôt créera une notice réelle dans HAL.")
    else:
        try:
            attempt = execute_production_deposit(deposit=deposit, actor=request.user)
        except (HALDuplicateError, HALSubmissionError) as exc:
            messages.error(request, str(exc))
        else:
            deposit.refresh_from_db()
            if attempt.accepted:
                messages.success(request, "HAL a accepté le dépôt réel.")
            elif deposit.state == HALProductionDeposit.State.UNCERTAIN:
                messages.error(request, "Résultat incertain : vérification manuelle requise.")
            else:
                messages.error(request, "HAL a refusé le dépôt réel.")
    return redirect("hal-production-deposit", deposit_id=deposit.id)


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

    Generated locally by the pinned package from the current reviewed metadata,
    with the immutable source row retained as provenance and fallback data. It
    performs no HAL request and is not a submission route.
    """
    publication = get_object_or_404(
        Publication.objects.prefetch_related("source_records__source_import"),
        id=publication_id,
    )
    source_record = publication.source_records.order_by("-created_at").first()
    submission = build_submission_xml(
        source_record.raw_data if source_record is not None else {},
        publication=publication,
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
