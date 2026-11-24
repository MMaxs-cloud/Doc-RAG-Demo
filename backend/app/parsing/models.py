from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

Format = Literal["pdf", "doc", "docx", "txt", "md", "html"]
FORMATS = ("pdf", "doc", "docx", "txt", "md", "html")


class SourceLocation(BaseModel):
    block_id: str
    start_offset: int
    end_offset: int
    section_path: list[str] | None = None
    page_number: int | None = None
    paragraph_index: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    source_start: int | None = None
    source_end: int | None = None
    html_element_id: str | None = None
    coordinates: dict[str, Any] | None = None


class SourceBlock(SourceLocation):
    block_type: str
    text: str
    line_aligned: bool = False


class CanonicalDocument(BaseModel):
    document_id: str
    original_filename: str
    original_format: Format
    mime_type: str
    sha256: str
    file_size: int
    original_file_path: str
    preview_file_path: str | None = None
    canonical_text: str
    metadata: dict[str, Any]
    source_blocks: list[SourceBlock]
    source_mapping: dict[str, tuple[int, int]]
    parser_name: str
    parser_version: str
    encoding: str | None = None
    source_text: str | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: float
    updated_at: float


@dataclass
class Extracted:
    metadata: dict[str, Any] = field(default_factory=dict)
    encoding: str | None = None
    source_text: str | None = None
    preview_format: str | None = None
    warnings: list[str] = field(default_factory=list)


class CanonicalBuilder:
    def __init__(self):
        self.blocks: list[SourceBlock] = []
        self.offset = 0
        self.sections: list[tuple[int, str]] = []

    def add(self, text: str, kind: str, *, heading: int | None = None, preserve=False, **coords):
        if not preserve:
            text = text.strip()
        if not text.strip():
            return
        if len(self.blocks) >= 100_000 or self.offset + len(text) > 25_000_000:
            from app.core.errors import AppError
            raise AppError("canonical_limit", "Объём извлечённого текста превышает лимит")
        if heading:
            self.sections = [(level, title) for level, title in self.sections if level < heading]
            self.sections.append((heading, text))
        end = self.offset + len(text)
        self.blocks.append(SourceBlock(
            block_id=f"b{len(self.blocks) + 1}", block_type=kind, text=text,
            start_offset=self.offset, end_offset=end,
            section_path=[title for _, title in self.sections] or None, **coords,
        ))
        self.offset = end + 2

    def build(self, record, extracted: Extracted, parser: str, version: str, preview: str | None):
        return CanonicalDocument(
            document_id=record["id"], original_filename=record["filename"],
            original_format=record["format"], mime_type=record["mime"], sha256=record["sha256"],
            file_size=record["size"], original_file_path=f"documents/{record['id']}/original",
            preview_file_path=preview, canonical_text="\n\n".join(b.text for b in self.blocks),
            metadata=extracted.metadata, source_blocks=self.blocks,
            source_mapping={b.block_id: (b.start_offset, b.end_offset) for b in self.blocks},
            parser_name=parser, parser_version=version, encoding=extracted.encoding,
            source_text=extracted.source_text, warnings=extracted.warnings,
            created_at=record["created_at"], updated_at=record["updated_at"],
        )
