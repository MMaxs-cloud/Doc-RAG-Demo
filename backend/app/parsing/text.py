import codecs
import re
import unicodedata
from pathlib import Path

from charset_normalizer import from_bytes
from markdown_it import MarkdownIt

from app.core.errors import AppError
from app.parsing.base import DocumentParser
from app.parsing.models import Extracted

LEGACY = ("cp1251", "cp1252", "koi8-r")
ENCODINGS = ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", *LEGACY)
RUSSIAN_COMMON = {
    "ст", "но", "то", "на", "ен", "ов", "ни", "ра", "ко", "ро", "по", "пр", "ре", "ер",
    "ос", "ол", "го", "те", "ва", "от", "ли", "ет", "ал", "ри", "ит", "ед", "ил", "ом",
    "ый", "ая", "ие", "ых", "ки", "ле", "ел", "ме", "ца", "ци", "де", "нт", "тр", "ур",
    "ем", "пе", "ес", "ед", "да", "им", "не", "сь", "об", "од", "чу", "ду", "ын", "ка",
}


def checked_decode(raw: bytes, encoding: str) -> str:
    try:
        result = raw.decode(encoding, errors="strict")
    except UnicodeError:
        raise AppError("invalid_encoding", f"Файл содержит недопустимые последовательности для {encoding}") from None
    invalid = next((i for i, ch in enumerate(result)
                    if unicodedata.category(ch) == "Cc" and ch not in "\t\n\r\f"), None)
    if invalid is not None:
        raise AppError("invalid_encoding", f"Недопустимый управляющий символ в позиции {invalid}")
    return result


def decode_source(path: Path, hint: str | None) -> tuple[str, str]:
    raw = path.read_bytes()
    if hint:
        try:
            encoding = codecs.lookup(hint).name
        except LookupError:
            encoding = ""
        if encoding not in ENCODINGS:
            raise AppError("unsupported_encoding", "Укажите UTF-8, UTF-16, Windows-1251, Windows-1252 или KOI8-R")
        return checked_decode(raw, encoding), encoding

    if raw.startswith(codecs.BOM_UTF8):
        return checked_decode(raw, "utf-8-sig"), "utf-8-sig"
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return checked_decode(raw, "utf-16"), "utf-16"
    if len(raw) >= 4 and len(raw) % 2 == 0:
        odd = raw[1::2].count(0) / (len(raw) // 2)
        even = raw[::2].count(0) / (len(raw) // 2)
        if max(odd, even) > 0.2 and abs(odd - even) > 0.18:
            encoding = "utf-16-le" if odd > even else "utf-16-be"
            return checked_decode(raw, encoding), encoding
    try:
        return checked_decode(raw, "utf-8"), "utf-8"
    except AppError:
        pass
    matches = from_bytes(raw, cp_isolation=list(LEGACY), threshold=0.2)
    candidates = []
    for encoding in LEGACY:
        try:
            text = checked_decode(raw, encoding)
        except AppError:
            continue
        words = re.findall(r"[А-Яа-яЁё]+", text)
        letters = sum(map(len, words))
        score = sum(sum(pair in RUSSIAN_COMMON for pair in
                        (word.lower()[i:i+2] for i in range(len(word)-1))) for word in words)
        if letters >= 12:
            candidates.append((score / letters, encoding, text))
    candidates.sort(reverse=True)
    if candidates and candidates[0][0] >= 0.30 and (
        len(candidates) == 1 or candidates[0][0] - candidates[1][0] >= 0.13
    ):
        _, encoding, text = candidates[0]
        return text, encoding
    best = matches.best()
    if best and best.encoding == "cp1252" and best.coherence >= 0.3:
        return checked_decode(raw, best.encoding), best.encoding
    raise AppError("encoding_ambiguous", "Кодировка неоднозначна. Укажите её вручную при повторной индексации.")


def line_entries(text: str):
    entries = text.splitlines(keepends=True)
    offsets = [0]
    for line in entries:
        offsets.append(offsets[-1] + len(line))
    return entries, offsets


class PlainTextParser(DocumentParser):
    formats = ("txt",)

    def parse(self, path, working, builder, config, encoding):
        text, encoding = decode_source(path, encoding)
        lines, offsets = line_entries(text)
        start = 0
        paragraph = 0
        for i, line in enumerate([*lines, ""]):
            if line.strip():
                continue
            if i > start:
                paragraph += 1
                fragment = text[offsets[start]:offsets[i]].rstrip("\r\n")
                builder.add(fragment, "paragraph", start_line=start + 1, end_line=i, preserve=True,
                            line_aligned=True, source_start=offsets[start], source_end=offsets[start] + len(fragment),
                            paragraph_index=paragraph)
            start = i + 1
        return Extracted(encoding=encoding, source_text=text)


def inline_text(tokens):
    parts = []
    links = []
    for token in tokens:
        if token.type in ("text", "code_inline", "image"):
            parts.append(token.content)
        elif token.type in ("softbreak", "hardbreak"):
            parts.append("\n")
        elif token.type == "link_open":
            links.append(token.attrGet("href") or "")
        elif token.type == "link_close" and links:
            url = links.pop()
            if url:
                parts.append(f" ({url})")
    return "".join(parts)


class MarkdownParser(DocumentParser):
    formats = ("md",)

    def parse(self, path, working, builder, config, encoding):
        text, encoding = decode_source(path, encoding)
        lines, offsets = line_entries(text)
        tokens = MarkdownIt("commonmark", {"html": False, "maxNesting": 64}).enable("table").parse(text)
        parents = []
        row = []
        table_range = None
        title = None
        paragraph = 0
        for token in tokens:
            if token.type.endswith("_open"):
                parents.append(token)
                if token.type == "table_open":
                    table_range = token.map
                continue
            if token.type.endswith("_close"):
                if token.type == "tr_close" and row:
                    start, end = parents[-1].map or table_range
                    builder.add(" | ".join(row), "table_row", start_line=start + 1, end_line=end,
                                source_start=offsets[start], source_end=offsets[end])
                    row.clear()
                parents.pop()
                continue
            if token.type not in {"inline", "fence", "code_block"}:
                continue
            span = token.map or next((parent.map for parent in reversed(parents) if parent.map), None)
            if span is None:
                continue
            start, end = span
            kind = "code" if token.type != "inline" else "paragraph"
            value = token.content if kind == "code" else inline_text(token.children or [])
            if any(p.type in {"td_open", "th_open"} for p in parents):
                row.append(value)
                continue
            heading = None
            if kind != "code" and parents:
                last = parents[-1]
                if last.type == "heading_open":
                    kind, heading = "heading", int(last.tag[1:])
                    title = title or value.strip()
                elif any(p.type == "list_item_open" for p in parents):
                    kind = "list_item"
                elif any(p.type == "blockquote_open" for p in parents):
                    kind = "quote"
            paragraph += 1
            builder.add(value, kind, heading=heading, start_line=start + 1, end_line=end,
                        source_start=offsets[start], source_end=offsets[end], paragraph_index=paragraph,
                        preserve=kind == "code")
        return Extracted(metadata={"title": title} if title else {}, encoding=encoding, source_text=text)
