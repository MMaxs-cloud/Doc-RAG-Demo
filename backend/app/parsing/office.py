import os
import shutil
import subprocess
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.core.errors import AppError
from app.parsing.base import DocumentParser
from app.parsing.detector import validate_docx
from app.parsing.models import Extracted

PROFILE = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
  <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
    <prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>
    <prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>
  </item>
  <item oor:path="/org.openoffice.Office.Writer/Content/Update">
    <prop oor:name="Link" oor:op="fuse"><value>0</value></prop>
  </item>
</oor:items>"""


def libreoffice(config) -> str | None:
    if config.soffice:
        return str(config.soffice.resolve()) if config.soffice.is_file() else None
    for executable in ("soffice.com", "soffice", "libreoffice"):
        binary = shutil.which(executable)
        if binary:
            return binary
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "LibreOffice/program/soffice.com"
        if candidate.is_file():
            return str(candidate)
    return None


def convert(path: Path, working: Path, config, target: str):
    binary = libreoffice(config)
    if not binary:
        raise AppError("libreoffice_missing", "Установите LibreOffice и задайте TRAG_SOFFICE или запустите контейнер parser.")
    if not (os.environ.get("TRAG_ISOLATED_PARSER") == "1" or config.allow_native_office):
        raise AppError("libreoffice_isolation_required", "Используйте изолированный parser или настройте блокировку сети LibreOffice.")
    directory = working / f"convert-{target}"
    directory.mkdir()
    profile = directory / "profile"
    (profile / "user").mkdir(parents=True)
    (profile / "user/registrymodifications.xcu").write_text(PROFILE, encoding="utf-8")
    source = directory / f"document{path.suffix}"
    shutil.copyfile(path, source)
    subprocess_env = dict(os.environ, HOME=str(directory), TMPDIR=str(directory),
                          TEMP=str(directory), TMP=str(directory), SAL_USE_VCLPLUGIN="svp")
    filter_name = {"docx": "docx:Office Open XML Text", "pdf": "pdf:writer_pdf_Export"}[target]
    try:
        result = subprocess.run(
            [binary, f"-env:UserInstallation={profile.resolve().as_uri()}", "--headless",
             "--nologo", "--nodefault", "--nofirststartwizard", "--norestore",
             "--convert-to", filter_name, "--outdir", str(directory), str(source)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=subprocess_env, cwd=directory, timeout=config.parsing.conversion_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise AppError("office_conversion_timeout", "Превышено время конвертации Word") from None
    except OSError:
        raise AppError("libreoffice_launch_failed", "Не удалось запустить LibreOffice. Проверьте TRAG_SOFFICE.") from None
    output = directory / f"document.{target}"
    if result.returncode or not output.is_file() or not output.stat().st_size:
        raise AppError("office_conversion_failed", "LibreOffice не смог обработать документ Word")
    return output


def office_text(paragraph):
    pieces = []
    for item in paragraph.iter_inner_content():
        if hasattr(item, "address"):
            url = item.url
            pieces.append(f"{item.text} ({url})" if url else item.text)
        else:
            pieces.append(item.text)
    return "".join(pieces)


def table_rows(table):
    for row in table.rows:
        cells = []
        seen = set()
        for cell in row.cells:
            if cell._tc in seen:
                cells.append("")
                continue
            seen.add(cell._tc)
            parts = []
            for item in cell.iter_inner_content():
                if isinstance(item, Table):
                    parts.extend(table_rows(item))
                else:
                    parts.append(office_text(item))
            cells.append("\n".join(part for part in parts if part.strip()))
        yield " | ".join(cells)


class DocxParser(DocumentParser):
    formats = ("docx",)

    def parse(self, path, working, builder, config, encoding):
        validate_docx(path, config.documents.max_docx_uncompressed_mb * 1024**2)
        try:
            document = Document(path)
        except (ValueError, KeyError, OSError) as exc:
            raise AppError("invalid_docx", "Не удалось прочитать структуру документа DOCX") from exc
        paragraph_index = 0
        table_index = 0
        for item in document.iter_inner_content():
            if isinstance(item, Table):
                table_index += 1
                for number, row in enumerate(table_rows(item), 1):
                    builder.add(row, "table_row", coordinates={"table_index": table_index, "table_row": number})
                continue
            if not isinstance(item, Paragraph):
                continue
            paragraph_index += 1
            style = item.style.name.lower() if item.style else ""
            outline = item._p.xpath("./w:pPr/w:outlineLvl/@w:val")
            heading = int(outline[0]) + 1 if outline and str(outline[0]).isdigit() and int(outline[0]) < 9 else None
            if style.startswith("heading ") and style.rsplit(" ", 1)[-1].isdigit():
                heading = int(style.rsplit(" ", 1)[-1])
            numbering = item._p.xpath("./w:pPr/w:numPr/w:numId/@w:val")
            if heading:
                kind = "heading"
            elif style.startswith("caption"):
                kind = "caption"
            elif style.startswith("list") or (numbering and numbering[0] != "0"):
                kind = "list_item"
            else:
                kind = "paragraph"
            builder.add(office_text(item), kind, heading=heading, paragraph_index=paragraph_index)
        props = document.core_properties
        metadata = {key: value for key, value in {
            "title": props.title, "authors": [props.author] if props.author else None,
            "language": props.language,
            "keywords": [part.strip() for part in props.keywords.split(",") if part.strip()],
        }.items() if value}
        extracted = Extracted(metadata=metadata)
        try:
            preview = convert(path, working, config, "pdf")
            preview.replace(working / "preview.pdf")
            extracted.preview_format = "pdf"
        except AppError as exc:
            extracted.warnings.append(exc.code)
        return extracted


class DocParser(DocumentParser):
    formats = ("doc",)

    def parse(self, path, working, builder, config, encoding):
        converted = convert(path, working, config, "docx")
        return DocxParser().parse(converted, working, builder, config, encoding)
