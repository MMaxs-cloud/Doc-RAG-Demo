import time
from uuid import uuid4
from zipfile import ZipFile

import pytest

from app.chunking import make_chunks
from app.core.errors import AppError
from app.parsing.detector import DocumentFormatDetector
from app.parsing.models import CanonicalBuilder, Extracted
from app.parsing.office import libreoffice
from app.parsing.registry import DocumentParserRegistry
from app.parsing.text import decode_source

from conftest import DOCUMENTS, FIXTURES


def record(fmt, name="test"):
    now = time.time()
    return dict(id=str(uuid4()), format=fmt, filename=name, mime="application/octet-stream",
                size=0, sha256="test", created_at=now, updated_at=now, encoding_hint=None)


@pytest.mark.parametrize("fmt", DOCUMENTS)
def test_document_parsing(fmt, tmp_path, settings):
    filename, expected = DOCUMENTS[fmt]
    if fmt == "doc" and (not (FIXTURES / filename).is_file()
                         or not libreoffice(settings) or not settings.allow_native_office):
        pytest.skip("Binary DOC requires an explicitly isolated LibreOffice")
    path = tmp_path / f"input.{fmt}"
    path.write_bytes((FIXTURES / filename).read_bytes())
    info = DocumentFormatDetector(256 * 1024**2).detect(path, filename)
    assert info.format == fmt
    parsed = DocumentParserRegistry().parse(tmp_path, {**record(fmt, filename), "mime": info.mime}, settings)
    assert expected in parsed.canonical_text
    assert parsed.source_blocks
    for block in parsed.source_blocks:
        assert parsed.canonical_text[block.start_offset:block.end_offset] == block.text
        assert parsed.source_mapping[block.block_id] == (block.start_offset, block.end_offset)
    if fmt == "html":
        assert "INACTIVE_PAYLOAD" not in parsed.canonical_text
        assert "reference thermocouple" in parsed.canonical_text
        assert any(block.html_element_id == "reference" for block in parsed.source_blocks)
        assert "<script" not in (tmp_path / "preview.html").read_text(encoding="utf-8")
    if fmt in ("doc", "docx"):
        assert all(block.page_number is None for block in parsed.source_blocks)
        if fmt == "docx":
            kinds = [block.block_type for block in parsed.source_blocks]
            assert kinds.index("table_row") < kinds.index("caption")
    if fmt == "pdf":
        assert {block.page_number for block in parsed.source_blocks} == {1, 2}
    if fmt in ("txt", "md"):
        assert all(block.start_line for block in parsed.source_blocks)
    if fmt == "md":
        assert {"heading", "table_row", "list_item", "quote", "code"} - {"list_item"} <= {
            block.block_type for block in parsed.source_blocks
        }
        assert "$e(t)" in parsed.canonical_text


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be",
                                       "cp1251", "cp1252", "koi8-r"])
def test_manual_encoding(tmp_path, encoding):
    text = "Café, épaisseur et résistance" if encoding == "cp1252" else "Контроллер температуры\nИспытания"
    path = tmp_path / "source.txt"
    path.write_bytes(text.encode(encoding))
    assert decode_source(path, encoding)[0] == text


def test_legacy_word_reports_missing_converter(tmp_path, settings):
    filename, _ = DOCUMENTS["doc"]
    path = tmp_path / "input.doc"
    path.write_bytes((FIXTURES / filename).read_bytes())
    assert DocumentFormatDetector(256 * 1024**2).detect(path, filename).format == "doc"
    missing = settings.model_copy(update={"soffice": tmp_path / "missing" / "soffice.exe"})
    with pytest.raises(AppError) as unavailable:
        DocumentParserRegistry().parse(tmp_path, record("doc", filename), missing)
    assert unavailable.value.code == "libreoffice_missing"


def test_invalid_encoding_does_not_replace(tmp_path):
    path = tmp_path / "source.txt"
    path.write_bytes(b"value \xff")
    with pytest.raises(AppError, match="недопустимые"):
        decode_source(path, "utf-8")


def test_detector_rejects_disguised_pdf_and_unsafe_zip(tmp_path):
    detector = DocumentFormatDetector(1000)
    path = tmp_path / "input"
    path.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(AppError) as error:
        detector.detect(path, "../../a.txt", "text/plain")
    assert error.value.code == "format_mismatch"
    with ZipFile(path, "w") as archive:
        for name in ("[Content_Types].xml", "word/document.xml", "_rels/.rels", "../../file"):
            archive.writestr(name, "x")
    with pytest.raises(AppError) as error:
        detector.detect(path, "a.docx")
    assert error.value.code == "invalid_docx"


def test_chunks_use_512_80_and_original_coordinates(vocab):
    builder = CanonicalBuilder()
    text = " ".join(f"измерение-{i}" for i in range(990))
    builder.add(text[:text.index("измерение-500")], "paragraph", page_number=1)
    builder.add(text[text.index("измерение-500"):], "paragraph", page_number=2)
    canonical = builder.build(record("pdf"), Extracted(), "test", "1", None)
    chunks = list(make_chunks(canonical, vocab, str(uuid4())))
    assert [c.tokens for c in chunks] == [512, 512, 126]
    assert chunks[0].text.split()[-80:] == chunks[1].text.split()[:80]
    assert chunks[0].start == 0 and chunks[-1].end == len(canonical.canonical_text)
    assert {loc.page_number for loc in chunks[0].locations} == {1, 2}
    for chunk in chunks:
        assert chunk.text == canonical.canonical_text[chunk.start:chunk.end]
