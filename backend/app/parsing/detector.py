import re
from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from lxml import etree

from app.core.errors import AppError
from app.parsing.models import Format

MIME = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain", "md": "text/markdown", "html": "text/html",
}
OLE = bytes.fromhex("d0cf11e0a1b11ae1")
HTML_TAG = re.compile(
    r"<!doctype\s+html\b|<\s*(?:html|body|head|title|main|article|section|div|p|pre|table|"
    r"h[1-6]|ul|ol|blockquote|figure|header|nav|span|[a-z][\w]*-[\w-]+)\b[^>]*>", re.I,
)
GENERIC_MIME = {"", "application/octet-stream", "binary/octet-stream", "application/download"}


@dataclass(frozen=True)
class FileInfo:
    filename: str
    format: Format
    mime: str


def safe_filename(raw: str | None) -> str:
    name = (raw or "").replace("\\", "/").split("/")[-1].strip()
    name = "".join(ch for ch in name if ord(ch) >= 32 and ch not in '\u007f\u202e\u202d')
    if not name or len(name) > 240:
        raise AppError("invalid_filename", "Недопустимое имя файла")
    return name


def validate_docx(path: Path, max_uncompressed: int):
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            if not {"[Content_Types].xml", "word/document.xml", "_rels/.rels"} <= names:
                raise ValueError
            if len(entries) > 4096 or len(names) != len(entries):
                raise ValueError
            size = 0
            for entry in entries:
                parts = entry.filename.split("/")
                if (
                    entry.filename.startswith("/") or "\\" in entry.filename or ":" in entry.filename
                    or ".." in parts or entry.flag_bits & 1
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000
                    or entry.filename.lower().endswith(("vbaproject.bin", "vbadata.xml"))
                ):
                    raise ValueError
                size += entry.file_size
                if size > max_uncompressed or entry.file_size > max(2_000_000, 250 * entry.compress_size):
                    raise AppError("docx_archive_limit", "DOCX превышает безопасный лимит распаковки")
            types = archive.getinfo("[Content_Types].xml")
            if types.file_size > 1_000_000:
                raise ValueError
            parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, recover=False)
            declarations = etree.fromstring(archive.read(types), parser)
            expected = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
            if declarations.getroottree().docinfo.doctype or not any(
                item.get("PartName") == "/word/document.xml" and item.get("ContentType") == expected
                for item in declarations
            ):
                raise ValueError
    except (BadZipFile, ValueError, etree.XMLSyntaxError, KeyError, RuntimeError):
        raise AppError("invalid_docx", "Файл не является поддерживаемым документом DOCX") from None


class DocumentFormatDetector:
    def __init__(self, max_uncompressed: int):
        self.max_uncompressed = max_uncompressed

    def detect(self, path: Path, raw_name: str | None, declared_mime: str | None = None) -> FileInfo:
        filename = safe_filename(raw_name)
        suffix = Path(filename).suffix.lower().lstrip(".")
        expected = "html" if suffix == "htm" else suffix
        if expected not in MIME:
            raise AppError("unsupported_format", "Поддерживаются PDF, DOC, DOCX, TXT, MD и HTML", 415)
        with path.open("rb") as file:
            head = file.read(8192)
        if not head:
            raise AppError("empty_file", "Файл пуст")
        if re.match(rb"^%PDF-\d\.\d", head):
            actual = "pdf"
        elif head.startswith(OLE):
            actual = "doc"
            import olefile
            try:
                with olefile.OleFileIO(path) as compound:
                    if not compound.exists("WordDocument") or not (
                        compound.exists("0Table") or compound.exists("1Table")
                    ):
                        raise ValueError
            except (OSError, ValueError, TypeError, IndexError):
                raise AppError("invalid_doc", "Бинарный файл не является документом Word") from None
        elif head.startswith(b"PK\x03\x04"):
            actual = "docx"
            validate_docx(path, self.max_uncompressed)
        else:
            unicode_bom = head.startswith((b"\xff\xfe", b"\xfe\xff"))
            alternating = len(head) > 3 and (
                head[::2].count(0) > len(head) // 5 or head[1::2].count(0) > len(head) // 5
            )
            if (b"\x00" in head and not unicode_bom and not alternating) or (
                not unicode_bom and not alternating
                and sum(byte < 32 and byte not in (9, 10, 12, 13) for byte in head) > max(0, len(head) // 50)
            ):
                raise AppError("invalid_text", "Обнаружены недопустимые двоичные данные", 415)
            if unicode_bom:
                sample = head.decode("utf-16", errors="ignore")
            elif alternating:
                endianness = "le" if head[1::2].count(0) > head[::2].count(0) else "be"
                sample = head.decode(f"utf-16-{endianness}", errors="ignore")
            else:
                sample = head.decode("latin1")
            if expected == "html":
                actual = "html" if HTML_TAG.search(sample) else "txt"
            elif expected == "txt" and re.search(r"^\s*(?:<!doctype\s+html|<html\b)", sample, re.I):
                actual = "html"
            else:
                actual = "md" if expected == "md" else "txt"
        if actual != expected:
            raise AppError("format_mismatch", f"Содержимое файла ({actual}) не соответствует расширению ({suffix})", 415)
        declared = (declared_mime or "").split(";", 1)[0].strip().lower()
        allowed = {MIME[actual]}
        if actual in {"txt", "md", "html"}:
            allowed.update({"text/plain", "text/x-markdown"} if actual == "md" else {"text/plain"})
        if actual == "docx":
            allowed.add("application/zip")
        if declared not in GENERIC_MIME | allowed:
            raise AppError("mime_mismatch", f"MIME-тип {declared} не соответствует формату {actual}", 415)
        return FileInfo(filename, actual, MIME[actual])
