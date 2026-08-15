from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unicodedata
from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from django.db import transaction
from django.utils.translation import gettext as _
from hal_assistant.sword import (
    PREPROD_URL,
    PRODUCTION_URL,
    SWORDResult,
    submit_notice,
    submit_production_notice,
)

from catalog.integrations.hal_assistant import build_submission_xml
from catalog.models import (
    AuditEvent,
    HALOperation,
    HALPayload,
    HALProductionAttempt,
    HALProductionDeposit,
    HALSubmissionAttempt,
    Publication,
)
from catalog.services.hal_credentials import HALCredentialError, credentials_for

HAL_SEARCH_URL = "https://api.archives-ouvertes.fr/search/hal/"
SEARCH_FIELDS = (
    "halId_s,title_s,producedDateY_i,authFullName_s,docType_s,doiId_s,journalTitle_s"
)


class HALSubmissionError(ValueError):
    pass


class HALDuplicateError(HALSubmissionError):
    def __init__(self, message: str, *, check: dict[str, object]):
        super().__init__(message)
        self.check = check


def _normalized(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def _first(value: object) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def _candidate_evidence(
    publication: Publication, candidate: dict[str, object]
) -> dict[str, object]:
    title_score = round(
        SequenceMatcher(
            None, _normalized(publication.title), _normalized(_first(candidate.get("title_s")))
        ).ratio(),
        3,
    )
    candidate_authors = candidate.get("authFullName_s") or []
    if isinstance(candidate_authors, str):
        candidate_authors = [candidate_authors]
    wanted_authors = {_normalized(value) for value in publication.authors}
    found_authors = {_normalized(value) for value in candidate_authors}
    author_match = bool(wanted_authors & found_authors)
    year_match = bool(
        publication.publication_year
        and candidate.get("producedDateY_i")
        and int(candidate["producedDateY_i"]) == publication.publication_year
    )
    type_match = bool(
        publication.hal_document_type
        and _first(candidate.get("docType_s")).upper()
        == publication.hal_document_type.upper()
    )
    doi_match = bool(
        publication.doi
        and _normalized(publication.doi)
        == _normalized(_first(candidate.get("doiId_s")))
    )
    corroborators = sum((year_match, author_match, type_match))
    classification = "clear"
    if doi_match or (title_score >= 0.92 and corroborators >= 2):
        classification = "probable_duplicate"
    elif title_score >= 0.92 or (title_score >= 0.80 and corroborators >= 1):
        classification = "needs_review"
    return {
        "hal_id": _first(candidate.get("halId_s")),
        "title": _first(candidate.get("title_s")),
        "title_score": title_score,
        "doi_match": doi_match,
        "year_match": year_match,
        "author_match": author_match,
        "type_match": type_match,
        "classification": classification,
    }


def check_live_duplicates(
    publication: Publication,
    *,
    opener: Callable[..., object] = urlopen,
    timeout: float = 20.0,
) -> dict[str, object]:
    """Fail-closed, multi-field live check before creating or sending a deposit."""
    query = f'title_t:"{publication.title.replace(chr(34), r"\"")}"'
    if publication.doi:
        query = f'({query}) OR doiId_s:"{publication.doi.replace(chr(34), r"\"")}"'
    params = {"q": query, "fl": SEARCH_FIELDS, "rows": 10, "wt": "json"}
    url = f"{HAL_SEARCH_URL}?{urlencode(params)}"
    try:
        with opener(url, timeout=timeout) as response:  # type: ignore[attr-defined]
            body = json.load(response)
    except Exception as exc:
        raise HALSubmissionError(
            _(
                "La vérification des doublons HAL est indisponible ; "
                "aucune soumission n’a été préparée."
            )
        ) from exc
    evidence = [
        _candidate_evidence(publication, candidate)
        for candidate in body.get("response", {}).get("docs", [])
    ]
    blocked = [item for item in evidence if item["classification"] != "clear"]
    check: dict[str, object] = {
        "query_url": url,
        "algorithm": "multifield-v1",
        "candidates": evidence,
        "blocked": bool(blocked),
    }
    if blocked:
        raise HALDuplicateError(
            _(
                "HAL contient un doublon probable ou une correspondance à examiner. "
                "La nouvelle notice est bloquée."
            ),
            check=check,
        )
    return check


def _submission_for(publication: Publication):
    source = publication.source_records.order_by("-created_at").first()
    if source is None:
        raise HALSubmissionError(_("Aucune source immuable n’est associée à cette notice."))
    submission = build_submission_xml(source.raw_data, publication=publication)
    if not submission.xml or submission.errors:
        details = "; ".join(submission.errors) or _("XML vide")
        raise HALSubmissionError(_("La notice XML n’est pas valide : %(details)s") % {"details": details})
    return submission


def _require_new_deposit_ready(publication: Publication) -> None:
    if publication.hal_id:
        raise HALSubmissionError(_("Cette notice possède déjà un identifiant HAL."))
    if publication.readiness_state != Publication.ReadinessState.HAL_READY:
        raise HALSubmissionError(_("La notice n’est pas marquée prête pour HAL."))
    if publication.missing_required_fields:
        raise HALSubmissionError(_("Des champs obligatoires sont encore manquants."))


def prepare_preprod_operation(
    *,
    publication: Publication,
    actor,
    duplicate_checker: Callable[[Publication], dict[str, object]] = check_live_duplicates,
) -> HALOperation:
    _require_new_deposit_ready(publication)
    submission = _submission_for(publication)
    try:
        duplicate_check = duplicate_checker(publication)
    except HALDuplicateError as exc:
        AuditEvent.objects.create(
            actor=actor,
            action="hal.preprod.duplicate_blocked",
            object_type="publication",
            object_id=str(publication.id),
            metadata=exc.check,
        )
        raise
    content = submission.xml
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    with transaction.atomic():
        locked = Publication.objects.select_for_update().get(pk=publication.pk)
        _require_new_deposit_ready(locked)
        existing = locked.hal_operations.filter(
            state=HALOperation.State.PREPARED,
            publication_version=locked.version,
        ).first()
        if existing:
            return existing
        operation = HALOperation.objects.create(
            publication=locked,
            requested_by=actor,
            publication_version=locked.version,
            state=HALOperation.State.PREPARED,
            duplicate_check=duplicate_check,
        )
        HALPayload.objects.create(
            operation=operation,
            content=content,
            sha256=digest,
            validation_errors=[],
        )
        AuditEvent.objects.create(
            actor=actor,
            action="hal.preprod.prepared",
            object_type="hal_operation",
            object_id=str(operation.id),
            after_checksum=digest,
            metadata={"publication_id": str(locked.id), "environment": "preprod"},
        )
    return operation


def _sanitized(value: str | None, *, secrets: tuple[str, ...] = ()) -> str:
    text = value or ""
    text = re.sub(r"(?i)authorization\s*:\s*[^\r\n]+", "Authorization: [redacted]", text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text[:20000]


def execute_preprod_operation(
    *,
    operation: HALOperation,
    actor,
    duplicate_checker: Callable[[Publication], dict[str, object]] = check_live_duplicates,
    submitter: Callable[..., SWORDResult] = submit_notice,
) -> HALSubmissionAttempt:
    publication = Publication.objects.get(pk=operation.publication_id)
    _require_new_deposit_ready(publication)
    try:
        credential = credentials_for(actor)
    except HALCredentialError as exc:
        raise HALSubmissionError(str(exc)) from exc
    if operation.publication_version != publication.version:
        raise HALSubmissionError(
            _("La notice a changé depuis la préparation ; préparez un nouveau contrôle.")
        )
    duplicate_checker(publication)
    with transaction.atomic():
        locked = HALOperation.objects.select_for_update().get(pk=operation.pk)
        if locked.state != HALOperation.State.PREPARED:
            raise HALSubmissionError(_("Cette opération a déjà été exécutée."))
        locked.state = HALOperation.State.SUBMITTING
        locked.save(update_fields=["state", "updated_at"])

    payload = operation.payload
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as handle:
            handle.write(payload.content.encode("utf-8"))
            path = Path(handle.name)
        result = submitter(
            path,
            environment="preprod",
            test=True,
            login=credential.login,
            password=credential.password,
        )
    except Exception as exc:
        result = SWORDResult(
            xml_file=path.name if path else "payload.xml",
            status_code=None,
            accepted=False,
            error=str(exc),
            sha256=payload.sha256,
        )
    finally:
        if path is not None:
            path.unlink(missing_ok=True)

    with transaction.atomic():
        attempt = HALSubmissionAttempt.objects.create(
            operation=operation,
            payload=payload,
            requested_by=actor,
            endpoint=PREPROD_URL,
            status_code=result.status_code,
            accepted=result.accepted,
            returned_hal_id=result.hal_id or "",
            returned_hal_url=result.hal_url or "",
            response_body=_sanitized(
                result.response_body, secrets=(credential.login, credential.password)
            ),
            error=_sanitized(result.error, secrets=(credential.login, credential.password)),
        )
        locked = HALOperation.objects.select_for_update().get(pk=operation.pk)
        locked.state = (
            HALOperation.State.ACCEPTED if result.accepted else HALOperation.State.REJECTED
        )
        locked.save(update_fields=["state", "updated_at"])
        if result.accepted:
            Publication.objects.filter(pk=publication.pk).update(
                readiness_state=Publication.ReadinessState.PREPROD_VALIDATED,
                hal_status="preprod_validated",
            )
        AuditEvent.objects.create(
            actor=actor,
            action=("hal.preprod.accepted" if result.accepted else "hal.preprod.rejected"),
            object_type="hal_submission_attempt",
            object_id=str(attempt.id),
            before_checksum=payload.sha256,
            after_checksum=payload.sha256,
            metadata={
                "publication_id": str(publication.id),
                "status_code": result.status_code,
                "environment": "preprod",
            },
        )
    return attempt


def prepare_production_deposit(
    *,
    preprod_operation: HALOperation,
    actor,
    duplicate_checker: Callable[[Publication], dict[str, object]] = check_live_duplicates,
) -> HALProductionDeposit:
    publication = Publication.objects.get(pk=preprod_operation.publication_id)
    if publication.hal_id:
        raise HALSubmissionError(_("Cette notice possède déjà un identifiant HAL."))
    if preprod_operation.state != HALOperation.State.ACCEPTED:
        raise HALSubmissionError(_("Le test en préproduction doit d’abord être accepté."))
    if preprod_operation.publication_version != publication.version:
        raise HALSubmissionError(
            _("La notice a changé depuis le test ; relancez la préproduction.")
        )
    payload = preprod_operation.payload
    accepted_attempt = preprod_operation.attempts.filter(accepted=True).first()
    if accepted_attempt is None or accepted_attempt.payload_id != payload.id:
        raise HALSubmissionError(_("Aucun reçu de préproduction accepté ne correspond au XML."))
    duplicate_check = duplicate_checker(publication)
    existing = HALProductionDeposit.objects.filter(
        preprod_operation=preprod_operation
    ).first()
    if existing:
        return existing
    with transaction.atomic():
        deposit = HALProductionDeposit.objects.create(
            publication=publication,
            preprod_operation=preprod_operation,
            requested_by=actor,
            publication_version=publication.version,
            payload_sha256=payload.sha256,
            duplicate_check=duplicate_check,
            state=HALProductionDeposit.State.PREPARED,
        )
        AuditEvent.objects.create(
            actor=actor,
            action="hal.production.prepared",
            object_type="hal_production_deposit",
            object_id=str(deposit.id),
            before_checksum=payload.sha256,
            after_checksum=payload.sha256,
            metadata={"publication_id": str(publication.id), "environment": "production"},
        )
    return deposit


def execute_production_deposit(
    *,
    deposit: HALProductionDeposit,
    actor,
    duplicate_checker: Callable[[Publication], dict[str, object]] = check_live_duplicates,
    submitter: Callable[..., SWORDResult] = submit_production_notice,
) -> HALProductionAttempt:
    publication = Publication.objects.get(pk=deposit.publication_id)
    if publication.hal_id:
        raise HALSubmissionError(_("Cette notice possède déjà un identifiant HAL."))
    if deposit.publication_version != publication.version:
        raise HALSubmissionError(_("La notice a changé ; le dépôt préparé est obsolète."))
    if deposit.preprod_operation.state != HALOperation.State.ACCEPTED:
        raise HALSubmissionError(_("Le test en préproduction n’est plus valide."))
    if deposit.payload_sha256 != deposit.preprod_operation.payload.sha256:
        raise HALSubmissionError(_("Le XML ne correspond plus au test accepté."))
    try:
        credential = credentials_for(actor)
    except HALCredentialError as exc:
        raise HALSubmissionError(str(exc)) from exc
    duplicate_checker(publication)
    with transaction.atomic():
        locked = HALProductionDeposit.objects.select_for_update().get(pk=deposit.pk)
        if locked.state != HALProductionDeposit.State.PREPARED:
            raise HALSubmissionError(_("Ce dépôt de production a déjà été exécuté."))
        locked.state = HALProductionDeposit.State.SUBMITTING
        locked.save(update_fields=["state", "updated_at"])

    payload = deposit.preprod_operation.payload
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as handle:
            handle.write(payload.content.encode("utf-8"))
            path = Path(handle.name)
        result = submitter(
            path,
            login=credential.login,
            password=credential.password,
            expected_sha256=payload.sha256,
            confirmation="SUBMIT_TO_HAL",
        )
    except Exception as exc:
        result = SWORDResult(
            xml_file=path.name if path else "payload.xml",
            status_code=None,
            accepted=False,
            error=str(exc),
            sha256=payload.sha256,
        )
    finally:
        if path is not None:
            path.unlink(missing_ok=True)

    confirmed = bool(result.accepted and result.hal_id)
    if confirmed:
        state = HALProductionDeposit.State.ACCEPTED
    elif result.status_code is None or result.accepted:
        state = HALProductionDeposit.State.UNCERTAIN
    else:
        state = HALProductionDeposit.State.REJECTED
    with transaction.atomic():
        attempt = HALProductionAttempt.objects.create(
            deposit=deposit,
            payload=payload,
            requested_by=actor,
            endpoint=PRODUCTION_URL,
            status_code=result.status_code,
            accepted=confirmed,
            returned_hal_id=result.hal_id or "",
            returned_hal_url=result.hal_url or "",
            response_body=_sanitized(
                result.response_body, secrets=(credential.login, credential.password)
            ),
            error=_sanitized(result.error, secrets=(credential.login, credential.password)),
        )
        locked = HALProductionDeposit.objects.select_for_update().get(pk=deposit.pk)
        locked.state = state
        locked.save(update_fields=["state", "updated_at"])
        if confirmed:
            Publication.objects.filter(pk=publication.pk, hal_id="").update(
                hal_id=result.hal_id,
                hal_status="submitted",
                readiness_state=Publication.ReadinessState.PRODUCTION_SUBMITTED,
                hal_synced_version=publication.version,
            )
        AuditEvent.objects.create(
            actor=actor,
            action=f"hal.production.{state}",
            object_type="hal_production_attempt",
            object_id=str(attempt.id),
            before_checksum=payload.sha256,
            after_checksum=payload.sha256,
            metadata={
                "publication_id": str(publication.id),
                "status_code": result.status_code,
                "environment": "production",
                "hal_id": result.hal_id or "",
            },
        )
    return attempt
