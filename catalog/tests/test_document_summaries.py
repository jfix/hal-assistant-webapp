from __future__ import annotations

import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from docx import Document

from catalog.services.document_summaries import (
    DocumentSummaryError,
    _response_text,
    _retry_delay,
    document_sha256,
    extract_document_text,
    extract_document_title,
    generate_bilingual_summary,
    summary_model,
)


def _upload(contents: bytes, name: str = "article.docx") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, contents)


def _docx(*paragraphs: tuple[str, str | None], core_title: str = "") -> bytes:
    document = Document()
    document.core_properties.title = core_title
    for text, style in paragraphs:
        document.add_paragraph(text, style=style)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _structured_result(**overrides) -> dict:
    result = {
        "abstract_en": " English abstract. ",
        "abstract_fr": " Résumé français. ",
        "keywords_en": [f" keyword {index} " for index in range(12)],
        "keywords_fr": [f" mot {index} " for index in range(12)],
        "suggested_title": " Suggested title ",
        "suggested_authors": [" Florence Fix ", ""],
        "suggested_publication_year": 2025,
        "suggested_publication_type": " article ",
        "suggested_doi": " 10.1234/example ",
    }
    result.update(overrides)
    return result


def test_document_hash_rewinds_upload() -> None:
    upload = _upload(b"immutable source bytes")

    digest = document_sha256(upload)

    assert digest == "a52acc5e404378c03c6bb2cdc70d04621c0e68b2ca952cb350b9a41c14b63396"
    assert upload.tell() == 0
    assert upload.read() == b"immutable source bytes"


def test_summary_model_uses_environment_override(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert summary_model() == "gpt-5.6-terra"
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    assert summary_model() == "test-model"


def test_docx_extraction_succeeds_and_normalizes_blank_lines() -> None:
    contents = _docx(
        ("  A humanities article about archives and memory. " * 8, None),
        ("", None),
        (" A concluding discussion of evidence and interpretation. " * 5, None),
    )

    text = extract_document_text(_upload(contents))

    assert text.startswith("A humanities article")
    assert "\n\n" not in text
    assert len(text) >= 200


def test_pdf_extraction_stops_after_character_budget(monkeypatch) -> None:
    monkeypatch.setattr("catalog.services.document_summaries.MAX_DOCUMENT_CHARACTERS", 220)
    pages = [Mock(extract_text=Mock(return_value="page text " * 15)) for _ in range(3)]
    reader = SimpleNamespace(is_encrypted=False, pages=pages)

    with patch("catalog.services.document_summaries.PdfReader", return_value=reader):
        text = extract_document_text(_upload(b"%PDF-mocked", "article.pdf"))

    assert len(text) == 220
    assert pages[0].extract_text.called
    assert pages[1].extract_text.called
    assert not pages[2].extract_text.called


@pytest.mark.parametrize("name", ["article.txt", "article.doc"])
def test_unsupported_document_extension_is_rejected(name) -> None:
    with pytest.raises(DocumentSummaryError, match="Formats acceptés"):
        extract_document_text(_upload(b"content", name))


def test_upload_size_limit_is_checked_before_reading(monkeypatch) -> None:
    monkeypatch.setattr("catalog.services.document_summaries.MAX_UPLOAD_BYTES", 5)
    upload = _upload(b"too large")

    with pytest.raises(DocumentSummaryError, match="20 Mo"):
        extract_document_text(upload)

    assert upload.tell() == 0


def test_docx_requires_word_archive_structure() -> None:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "content types only")

    with pytest.raises(DocumentSummaryError, match="structure Word valide"):
        extract_document_text(_upload(output.getvalue()))


def test_docx_requires_zip_signature() -> None:
    with pytest.raises(DocumentSummaryError, match="document Word DOCX"):
        extract_document_text(_upload(b"not a zip archive" * 30))


def test_docx_entry_and_uncompressed_size_limits(monkeypatch) -> None:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "x")
        archive.writestr("word/document.xml", "document")
        archive.writestr("extra.xml", "extra")

    monkeypatch.setattr("catalog.services.document_summaries.MAX_DOCX_ENTRIES", 2)
    with pytest.raises(DocumentSummaryError, match="trop d’éléments"):
        extract_document_text(_upload(output.getvalue()))

    monkeypatch.setattr("catalog.services.document_summaries.MAX_DOCX_ENTRIES", 10)
    monkeypatch.setattr("catalog.services.document_summaries.MAX_DOCX_UNCOMPRESSED_BYTES", 5)
    with pytest.raises(DocumentSummaryError, match="décompressé"):
        extract_document_text(_upload(output.getvalue()))


def test_short_extractable_document_explains_ocr_requirement() -> None:
    contents = _docx(("Only a short paragraph.", None))

    with pytest.raises(DocumentSummaryError, match="OCR"):
        extract_document_text(_upload(contents))


def test_unexpected_parser_failure_is_wrapped_in_safe_error() -> None:
    with (
        patch("catalog.services.document_summaries._validate_docx_archive"),
        patch("catalog.services.document_summaries.Document", side_effect=ValueError("detail")),
    ):
        with pytest.raises(DocumentSummaryError, match="Impossible de lire") as caught:
            extract_document_text(_upload(b"PK parser failure"))

    assert "detail" not in str(caught.value)


def test_docx_embedded_title_precedes_visible_title_style() -> None:
    contents = _docx(
        ("A Different Visible Document Title", "Title"),
        ("A substantial humanities discussion. " * 10, None),
        core_title="The Credible Embedded Document Title",
    )
    upload = _upload(contents)

    assert extract_document_title(upload, "Fallback textual title\nBody") == (
        "The Credible Embedded Document Title"
    )
    assert upload.tell() == 0


def test_pdf_embedded_title_is_used_when_credible() -> None:
    upload = _upload(b"%PDF-mocked", "article.pdf")
    metadata = SimpleNamespace(title="The Embedded PDF Article Title")
    with patch(
        "catalog.services.document_summaries.PdfReader",
        return_value=SimpleNamespace(metadata=metadata),
    ):
        title = extract_document_title(upload, "A Weaker Textual Candidate")

    assert title == "The Embedded PDF Article Title"
    assert upload.tell() == 0


def test_title_metadata_failure_falls_back_to_text_and_rewinds() -> None:
    upload = _upload(b"PK malformed archive")

    title = extract_document_title(
        upload,
        "A Reliable Title Recovered From Extracted Text\nBody content",
    )

    assert title == "A Reliable Title Recovered From Extracted Text"
    assert upload.tell() == 0


def test_response_text_supports_nested_responses_api_shape() -> None:
    assert _response_text(
        {
            "output": [
                {"content": [{"type": "refusal", "text": "ignored"}]},
                {"content": [{"type": "output_text", "text": '{"ok": true}'}]},
            ]
        }
    ) == '{"ok": true}'


def test_response_text_rejects_empty_provider_payload() -> None:
    with pytest.raises(DocumentSummaryError, match="réponse vide"):
        _response_text({"output": [{"content": []}]})


@pytest.mark.parametrize(
    ("retry_after", "fallback", "expected"),
    [("25", 1.0, 10.0), ("-2", 1.0, 0.0), ("invalid", 3.0, 3.0), (None, 2.0, 2.0)],
)
def test_retry_delay_is_bounded_and_tolerates_invalid_header(
    retry_after, fallback, expected
) -> None:
    error = HTTPError("https://api.openai.com", 429, "limited", {}, BytesIO())
    if retry_after is not None:
        error.headers["Retry-After"] = retry_after

    assert _retry_delay(error, fallback) == expected


def test_retry_delay_without_http_error_uses_fallback() -> None:
    assert _retry_delay(None, 3.0) == 3.0


def test_generation_normalizes_and_limits_structured_result(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    response = {"output_text": json.dumps(_structured_result())}

    with patch("catalog.services.document_summaries._openai_response", return_value=response):
        summary = generate_bilingual_summary("source text")

    assert summary.abstract_en == "English abstract."
    assert summary.keywords_en == [f"keyword {index}" for index in range(10)]
    assert summary.keywords_fr == [f"mot {index}" for index in range(10)]
    assert summary.suggested_authors == ["Florence Fix"]
    assert summary.suggested_title == "Suggested title"
    assert summary.suggested_doi == "10.1234/example"


@pytest.mark.parametrize(
    "provider_text",
    ["not json", json.dumps({"abstract_en": "incomplete"}), json.dumps([])],
)
def test_generation_rejects_malformed_structured_result(monkeypatch, provider_text) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with patch(
        "catalog.services.document_summaries._openai_response",
        return_value={"output_text": provider_text},
    ):
        with pytest.raises(DocumentSummaryError, match="format attendu"):
            generate_bilingual_summary("source text")


def test_generation_requires_api_configuration(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(DocumentSummaryError, match="OPENAI_API_KEY"):
        generate_bilingual_summary("source text")


@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "configuration"), (503, "momentanément indisponible")],
)
def test_provider_auth_and_exhausted_server_errors_are_user_safe(
    monkeypatch, status, message
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    error = HTTPError("https://api.openai.com", status, "private detail", {}, BytesIO())
    with (
        patch("catalog.services.document_summaries.urlopen", side_effect=error),
        patch("catalog.services.document_summaries.time.sleep"),
    ):
        with pytest.raises(DocumentSummaryError, match=message) as caught:
            generate_bilingual_summary("source text")

    assert "private detail" not in str(caught.value)
