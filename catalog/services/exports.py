from __future__ import annotations

from collections.abc import Iterable
from io import BytesIO
from typing import Any

from openpyxl import Workbook

from catalog.models import Publication

# One-way snapshot export. Headers match what the snapshot importer reads, so a
# re-import lands as a tracked, diffed changeset rather than a silent two-way
# sync. (field, header) pairs, in column order.
EXPORT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("publication_key", "publication_id"),
    ("review_state", "decision"),
    ("publication_type", "publication_type"),
    ("hal_document_type", "document_type"),
    ("title", "title"),
    ("publication_year", "year"),
    ("authors", "authors"),
    ("editors", "editors"),
    ("language", "language"),
    ("abstract_en", "abstract_en"),
    ("abstract_fr", "abstract_fr"),
    ("keywords_en", "keywords_en"),
    ("keywords_fr", "keywords_fr"),
    ("journal_title", "journal_title"),
    ("book_title", "book_title"),
    ("publisher", "publisher"),
    ("publisher_city", "publisher_city"),
    ("volume", "volume"),
    ("issue", "issue"),
    ("pages", "pages"),
    ("doi", "doi"),
    ("isbn", "isbn"),
    ("issn", "issn"),
    ("conference_title", "conference_title"),
    ("conference_start_date", "conference_start_date"),
    ("conference_end_date", "conference_end_date"),
    ("conference_city", "conference_city"),
    ("conference_country", "conference_country"),
    ("source_url", "source_url"),
    ("readiness_state", "readiness_state"),
    ("hal_id", "hal_id"),
    ("hal_status", "hal_status"),
)


def _cell(value: Any) -> Any:
    if isinstance(value, list | tuple):
        return "; ".join(str(item) for item in value)
    if value is None:
        return ""
    return value


def export_publications_xlsx(publications: Iterable[Publication]) -> bytes:
    """Serialize publications to an XLSX snapshot with a Publications sheet."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Publications"
    sheet.append([header for _, header in EXPORT_COLUMNS])
    for publication in publications:
        sheet.append([_cell(getattr(publication, field)) for field, _ in EXPORT_COLUMNS])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
