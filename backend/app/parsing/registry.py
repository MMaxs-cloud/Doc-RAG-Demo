import time
from pathlib import Path

from app.core.errors import AppError
from app.parsing.detector import FileInfo
from app.parsing.html import HtmlParser
from app.parsing.models import CanonicalBuilder
from app.parsing.office import DocParser, DocxParser
from app.parsing.pdf import PdfParser
from app.parsing.text import MarkdownParser, PlainTextParser


class DocumentParserRegistry:
    def __init__(self):
        self.parsers = (PdfParser(), DocxParser(), DocParser(), PlainTextParser(), MarkdownParser(), HtmlParser())

    def parser_for(self, info: FileInfo):
        for parser in self.parsers:
            if parser.supports(info):
                return parser
        raise AppError("unsupported_format", "Парсер отсутствует")

    def parse(self, working: Path, record: dict, config):
        info = FileInfo(record["filename"], record["format"], record["mime"])
        parser = self.parser_for(info)
        builder = CanonicalBuilder()
        extracted = parser.parse(working / f"input.{info.format}", working, builder, config, record["encoding_hint"])
        if not builder.blocks:
            raise AppError("no_text", "В документе нет текста для индексации")
        name, version = parser.get_parser_info()
        return builder.build({**record, "updated_at": time.time()}, extracted, name, version,
                             f"preview.{extracted.preview_format}" if extracted.preview_format else None)
