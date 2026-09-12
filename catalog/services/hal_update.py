from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from django.db import transaction
from django.utils.translation import gettext as _
from hal_assistant.update import UpdateResult, update_notice

from catalog.models import AuditEvent, HALUpdateAttempt, HALUpdateOperation, Publication
from catalog.services.hal_credentials import HALCredentialError, credentials_for
from catalog.services.hal_submission import HAL_SEARCH_URL, _submission_for


class HALUpdateError(ValueError):
    pass


def fetch_hal_document_version(
    hal_id: str,
    *,
    opener: Callable[..., object] = urlopen,
    timeout: float = 20.0,
) -> int:
    """Fail-closed lookup of the live HAL document version required by SWORD updates."""
    params = {"q": f'halId_s:"{hal_id}"', "fl": "halId_s,version_i", "wt": "json"}
    url = f"{HAL_SEARCH_URL}?{urlencode(params)}"
    try:
        with opener(url, timeout=timeout) as response:  # type: ignore[attr-defined]
            body = json.load(response)
    except Exception as exc:
        raise HALUpdateError(
            _("La version du document HAL est introuvable ; aucune mise à jour préparée.")
        ) from exc
    docs = body.get("response", {}).get("docs", [])
    if len(docs) != 1 or docs[0].get("halId_s") != hal_id:
        raise HALUpdateError(
            _("La notice %(hal_id)s est introuvable sur HAL.") % {"hal_id": hal_id}
        )
    version = docs[0].get("version_i")
    if not isinstance(version, int) or version < 1:
        raise HALUpdateError(
            _("HAL n’a pas renvoyé de version exploitable pour %(hal_id)s.")
            % {"hal_id": hal_id}
        )
    return version


def _require_update_ready(publication: Publication) -> None:
    if not publication.hal_id:
        raise HALUpdateError(_("Cette notice n’a pas d’identifiant HAL."))
    if publication.missing_required_fields:
        raise HALUpdateError(_("Des champs obligatoires sont encore manquants."))
    if publication.hal_synced_version == publication.version:
        raise HALUpdateError(
            _("Aucune modification locale à publier : la notice est déjà à jour sur HAL.")
        )


def prepare_update_operation(
    *,
    publication: Publication,
    actor,
    version_fetcher: Callable[[str], int] = fetch_hal_document_version,
) -> HALUpdateOperation:
    _require_update_ready(publication)
    submission = _submission_for(publication)
    content = submission.xml
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    hal_document_version = version_fetcher(publication.hal_id)
    with transaction.atomic():
        locked = Publication.objects.select_for_update().get(pk=publication.pk)
        _require_update_ready(locked)
        existing = locked.hal_update_operations.filter(
            state__in=[
                HALUpdateOperation.State.PREPARED,
                HALUpdateOperation.State.TEST_ACCEPTED,
            ],
            publication_version=locked.version,
            payload_sha256=digest,
            hal_document_version=hal_document_version,
        ).first()
        if existing:
            return existing
        operation = HALUpdateOperation.objects.create(
            publication=locked,
            requested_by=actor,
            publication_version=locked.version,
            hal_id=locked.hal_id,
            hal_document_version=hal_document_version,
            payload_content=content,
            payload_sha256=digest,
            state=HALUpdateOperation.State.PREPARED,
        )
        AuditEvent.objects.create(
            actor=actor,
            action="hal.update.prepared",
            object_type="hal_update_operation",
            object_id=str(operation.id),
            after_checksum=digest,
            metadata={
                "publication_id": str(locked.id),
                "hal_id": locked.hal_id,
                "hal_document_version": hal_document_version,
                "environment": "production",
            },
        )
    return operation


def _credential_for(actor):
    try:
        return credentials_for(actor)
    except HALCredentialError as exc:
        raise HALUpdateError(str(exc)) from exc


def _run_update(
    operation: HALUpdateOperation,
    *,
    credential,
    test: bool,
    updater: Callable[..., UpdateResult],
) -> UpdateResult:
    """Run one update request; never raises, so operation state cannot strand."""
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as handle:
            handle.write(operation.payload_content.encode("utf-8"))
            path = Path(handle.name)
        return updater(
            path,
            hal_id=operation.hal_id,
            hal_version=operation.hal_document_version,
            test=test,
            expected_sha256=operation.payload_sha256,
            confirmation=None if test else "UPDATE_EXISTING_HAL_RECORDS",
            login=credential.login,
            password=credential.password,
        )
    except Exception as exc:
        return UpdateResult(
            hal_id=operation.hal_id,
            version=operation.hal_document_version,
            xml_file=path.name if path else "payload.xml",
            sha256=operation.payload_sha256,
            target_url="",
            status_code=None,
            accepted=False,
            error=str(exc),
        )
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


def _record_attempt(
    operation: HALUpdateOperation, *, actor, test: bool, result: UpdateResult
) -> HALUpdateAttempt:
    return HALUpdateAttempt.objects.create(
        operation=operation,
        requested_by=actor,
        test_mode=test,
        target_url=result.target_url,
        payload_sha256=operation.payload_sha256,
        status_code=result.status_code,
        accepted=result.accepted,
        returned_hal_id=result.returned_hal_id or "",
        returned_hal_url=result.hal_url or "",
        error=result.error or "",
    )


def execute_update_test(
    *,
    operation: HALUpdateOperation,
    actor,
    updater: Callable[..., UpdateResult] = update_notice,
) -> HALUpdateAttempt:
    publication = Publication.objects.get(pk=operation.publication_id)
    if operation.publication_version != publication.version:
        raise HALUpdateError(
            _("La notice a changé depuis la préparation ; préparez une nouvelle mise à jour.")
        )
    if publication.hal_id != operation.hal_id:
        raise HALUpdateError(_("L’identifiant HAL de la notice a changé depuis la préparation."))
    credential = _credential_for(actor)
    with transaction.atomic():
        locked = HALUpdateOperation.objects.select_for_update().get(pk=operation.pk)
        if locked.state != HALUpdateOperation.State.PREPARED:
            raise HALUpdateError(_("Le test de cette mise à jour a déjà été exécuté."))
        locked.state = HALUpdateOperation.State.SUBMITTING
        locked.save(update_fields=["state", "updated_at"])

    result = _run_update(operation, credential=credential, test=True, updater=updater)

    with transaction.atomic():
        attempt = _record_attempt(operation, actor=actor, test=True, result=result)
        locked = HALUpdateOperation.objects.select_for_update().get(pk=operation.pk)
        locked.state = (
            HALUpdateOperation.State.TEST_ACCEPTED
            if result.accepted
            else HALUpdateOperation.State.REJECTED
        )
        locked.save(update_fields=["state", "updated_at"])
        AuditEvent.objects.create(
            actor=actor,
            action=(
                "hal.update.test_accepted" if result.accepted else "hal.update.test_rejected"
            ),
            object_type="hal_update_attempt",
            object_id=str(attempt.id),
            before_checksum=operation.payload_sha256,
            after_checksum=operation.payload_sha256,
            metadata={
                "publication_id": str(operation.publication_id),
                "hal_id": operation.hal_id,
                "status_code": result.status_code,
                "environment": "production",
            },
        )
    return attempt


def execute_update(
    *,
    operation: HALUpdateOperation,
    actor,
    updater: Callable[..., UpdateResult] = update_notice,
) -> HALUpdateAttempt:
    publication = Publication.objects.get(pk=operation.publication_id)
    if operation.publication_version != publication.version:
        raise HALUpdateError(
            _("La notice a changé depuis le test ; préparez une nouvelle mise à jour.")
        )
    if publication.hal_id != operation.hal_id:
        raise HALUpdateError(_("L’identifiant HAL de la notice a changé depuis la préparation."))
    credential = _credential_for(actor)
    with transaction.atomic():
        locked = HALUpdateOperation.objects.select_for_update().get(pk=operation.pk)
        if locked.state != HALUpdateOperation.State.TEST_ACCEPTED:
            raise HALUpdateError(_("Un test de mise à jour accepté est requis."))
        locked.state = HALUpdateOperation.State.SUBMITTING
        locked.save(update_fields=["state", "updated_at"])

    result = _run_update(operation, credential=credential, test=False, updater=updater)

    confirmed = bool(result.accepted)
    if confirmed:
        state = HALUpdateOperation.State.ACCEPTED
    elif result.status_code is None:
        state = HALUpdateOperation.State.UNCERTAIN
    else:
        state = HALUpdateOperation.State.REJECTED
    with transaction.atomic():
        attempt = _record_attempt(operation, actor=actor, test=False, result=result)
        locked = HALUpdateOperation.objects.select_for_update().get(pk=operation.pk)
        locked.state = state
        locked.save(update_fields=["state", "updated_at"])
        if confirmed:
            Publication.objects.filter(
                pk=operation.publication_id, version=operation.publication_version
            ).update(hal_synced_version=operation.publication_version)
        AuditEvent.objects.create(
            actor=actor,
            action=f"hal.update.{state}",
            object_type="hal_update_attempt",
            object_id=str(attempt.id),
            before_checksum=operation.payload_sha256,
            after_checksum=operation.payload_sha256,
            metadata={
                "publication_id": str(operation.publication_id),
                "hal_id": operation.hal_id,
                "hal_document_version": operation.hal_document_version,
                "status_code": result.status_code,
                "environment": "production",
            },
        )
    return attempt
