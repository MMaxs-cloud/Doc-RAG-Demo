import re

import pymupdf

from app.core.errors import AppError
from app.parsing.base import DocumentParser
from app.parsing.models import CanonicalBuilder, Extracted


def define_metadata(document):
    properties = document.metadata or {}
    metadata = {"page_count": len(document)}
    for source, target in (("title", "title"), ("author", "authors"), ("keywords", "keywords")):
        value = (properties.get(source) or "").strip()
        if value:
            metadata[target] = value if source == "title" else [value]
    return metadata


def from_docling(path, builder, config):
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode, TesseractCliOcrOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    if not config.docling_artifacts or not config.docling_artifacts.is_dir():
        raise AppError("docling_artifacts_missing", "Загрузите локальные артефакты Docling.")
    options = PdfPipelineOptions(
        artifacts_path=config.docling_artifacts,
        do_ocr=config.parsing.ocr_enabled, do_table_structure=True,
        enable_remote_services=False, allow_external_plugins=False,
        accelerator_options=AcceleratorOptions(device=config.parsing.pdf_device, num_threads=4),
        document_timeout=config.parsing.timeout_seconds - 5,
    )
    options.table_structure_options.mode = TableFormerMode.FAST
    if config.parsing.ocr_enabled:
        options.ocr_options = TesseractCliOcrOptions(lang=["rus", "eng"])
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    result = converter.convert(path, max_num_pages=1500)
    if result.status.value != "success":
        raise AppError("docling_failed", "Docling не завершил извлечение PDF.")
    parsed = result.document
    for item, _ in parsed.iterate_items():
        label = item.label.value
        if label in ("page_header", "page_footer"):
            continue
        value = getattr(item, "text", "")
        if label == "table":
            value = item.export_to_markdown(doc=parsed)
        if not value:
            continue
        provenance = list(getattr(item, "prov", []))
        heading = getattr(item, "level", 1) if label in {"title", "section_header"} else None
        if not provenance:
            builder.add(value, label, heading=heading)
            continue
        for point in provenance:
            start, end = point.charspan
            fragment = value[start:end] if len(provenance) > 1 and label != "table" else value
            if label == "table" and len(provenance) > 1:
                builder.add(fragment, label, coordinates={"pages": sorted({p.page_no for p in provenance})})
                break
            builder.add(fragment, label, heading=heading, page_number=point.page_no,
                        coordinates={"bbox": point.bbox.model_dump(mode="json")})


def from_pymupdf(document, builder):
    for page in document:
        rows = [block for block in page.get_text("blocks") if block[6] == 0]
        midpoint = page.rect.x0 + page.rect.width / 2
        gutter = page.rect.width * 0.025
        wide = sorted((b for b in rows if b[0] < midpoint - gutter < midpoint + gutter < b[2]),
                      key=lambda b: b[1])
        narrow = [block for block in rows if block not in wide]
        left = [b for b in narrow if (b[0] + b[2]) / 2 < midpoint]
        right = [b for b in narrow if b not in left]
        two_columns = bool(left and right and
                           max(min(b[1] for b in left), min(b[1] for b in right)) <
                           min(max(b[3] for b in left), max(b[3] for b in right)))
        if two_columns:
            ordered = []
            for full_width in [*wide, (0, float("inf"))]:
                end = full_width[1]
                ordered.extend(sorted((b for b in narrow if b[1] < end),
                                      key=lambda b: ((b[0] + b[2]) / 2 >= midpoint, b[1])))
                narrow = [b for b in narrow if b[1] >= end]
                if full_width in wide:
                    ordered.append(full_width)
        else:
            ordered = sorted(rows, key=lambda b: (b[1], b[0]))
        for x0, y0, x1, y1, text, *_ in ordered:
            builder.add(re.sub(r"(?<=\w)-\n(?=\w)", "", text), "paragraph",
                        page_number=page.number + 1,
                        coordinates={"bbox": [x0, y0, x1, y1], "origin": "top_left"})


class PdfParser(DocumentParser):
    formats = ("pdf",)

    def parse(self, path, working, builder, config, encoding):
        try:
            document = pymupdf.open(path)
        except (RuntimeError, ValueError):
            raise AppError("invalid_pdf", "Невозможно открыть структуру PDF") from None
        with document:
            if not document.is_pdf or document.needs_pass or not 0 < len(document) <= 1500:
                raise AppError("invalid_pdf", "PDF зашифрован, пуст или превышает лимит страниц")
            metadata = define_metadata(document)
            warnings = []
            try:
                from_docling(path, builder, config)
            except (AppError, ImportError, RuntimeError, OSError, ValueError) as exc:
                if not config.parsing.pdf_fallback:
                    if isinstance(exc, AppError):
                        raise
                    raise AppError("docling_failed", "Не удалось запустить Docling") from exc
                builder.blocks.clear()
                builder.sections.clear()
                builder.offset = 0
                from_pymupdf(document, builder)
                warnings.append(f"pdf_fallback:{getattr(exc, 'code', type(exc).__name__)}")
            if not builder.blocks:
                raise AppError("requires_ocr", "PDF не содержит извлекаемого текста. Включите OCR.")
            return Extracted(metadata=metadata, warnings=warnings)
