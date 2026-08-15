from __future__ import annotations

import os
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from django.db import transaction
from django.utils.translation import gettext as _

from catalog.models import AuditEvent, HALCredential


class HALCredentialError(RuntimeError):
    pass


@dataclass(frozen=True)
class DecryptedHALCredential:
    login: str
    password: str


def _fernet() -> Fernet:
    key = os.getenv("HAL_CREDENTIAL_ENCRYPTION_KEY", "")
    if not key:
        raise HALCredentialError(_("Le chiffrement des identifiants HAL n’est pas configuré."))
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise HALCredentialError(
            _("La clé de chiffrement des identifiants HAL est invalide.")
        ) from exc


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError) as exc:
        raise HALCredentialError(_("Les identifiants HAL enregistrés sont illisibles.")) from exc


def credentials_for(user) -> DecryptedHALCredential:
    try:
        stored = HALCredential.objects.get(user=user)
    except HALCredential.DoesNotExist as exc:
        raise HALCredentialError(
            _("Configurez vos identifiants HAL dans « Mon compte » avant de lancer le test.")
        ) from exc
    return DecryptedHALCredential(
        login=_decrypt(stored.encrypted_login),
        password=_decrypt(stored.encrypted_password),
    )


@transaction.atomic
def save_credentials(*, user, login: str, password: str) -> HALCredential:
    credential, created = HALCredential.objects.update_or_create(
        user=user,
        defaults={
            "encrypted_login": _encrypt(login.strip()),
            "encrypted_password": _encrypt(password),
        },
    )
    AuditEvent.objects.create(
        actor=user,
        action="hal_credentials.created" if created else "hal_credentials.updated",
        object_type="user",
        object_id=str(user.pk),
        metadata={},
    )
    return credential


@transaction.atomic
def delete_credentials(*, user) -> bool:
    deleted, _ = HALCredential.objects.filter(user=user).delete()
    if deleted:
        AuditEvent.objects.create(
            actor=user,
            action="hal_credentials.deleted",
            object_type="user",
            object_id=str(user.pk),
            metadata={},
        )
    return bool(deleted)


def saved_login_for(user) -> str:
    try:
        return credentials_for(user).login
    except HALCredentialError:
        return ""
