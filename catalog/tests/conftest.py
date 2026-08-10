from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from django.core.files.storage import storages
from openpyxl import Workbook


@pytest.fixture(autouse=True)
def isolated_media(settings, tmp_path: Path, monkeypatch) -> None:
    settings.MEDIA_ROOT = tmp_path / "media"
    storages._storages.clear()
    monkeypatch.setenv("HAL_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture
def workbook_factory(tmp_path: Path):
    counter = 0

    def make_workbook(
        rows: Iterable[dict[str, Any]],
        *,
        name: str | None = None,
    ) -> Path:
        nonlocal counter
        counter += 1
        path = tmp_path / (name or f"snapshot-{counter}.xlsx")
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Publications"
        row_list = list(rows)
        required_headers = [
            "publication_id",
            "decision",
            "publication_type",
            "title",
            "year",
            "authors",
        ]
        extra_headers = sorted(
            {key for row in row_list for key in row}.difference(required_headers)
        )
        headers = required_headers + extra_headers
        sheet.append(headers)
        for row in row_list:
            sheet.append([row.get(header) for header in headers])
        workbook.save(path)
        return path

    return make_workbook


@pytest.fixture
def reviewed_row() -> dict[str, Any]:
    return {
        "publication_id": "pub-0001",
        "decision": "approve",
        "publication_type": "journal_article",
        "title": "A locally managed publication",
        "year": 2025,
        "authors": "Ada Lovelace; Grace Hopper",
        "language": "en",
        "journal_title": "Journal of Portable Systems",
        "doi": "10.1234/example",
        "original_citation": "  Lovelace, Ada.  A citation with spacing.  ",
    }
