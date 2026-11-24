import re
from collections import Counter
from pathlib import Path

from app.chunking import source_slice
from app.core.errors import AppError
from app.parsing.models import CanonicalDocument

REFERENCE = re.compile(r"\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]", re.I)
REFERENCE_ID = re.compile(r"\bS\d+\b", re.I)
PROSE_REFERENCE = re.compile(
    r"(?P<label>\b(?:источник(?:а(?:х|ми)?|е|и|ов|ом|у|ам)?|sources?)\s*(?:№\s*)?)"
    r"(?P<ids>S\d+(?:(?:\s*[,;]\s*|\s+(?:и|and)\s+)S\d+)*)\b",
    re.I,
)
ANSWER_LINK = re.compile(r"\[([^\]\n]+)\]\((?:https?://|/)[^\s)]+\)|https?://[^\s<>\])]+", re.I)


def coordinates(locations):
    pages = [page for loc in locations for page in
             ([loc.page_number] if loc.page_number else (loc.coordinates or {}).get("pages", []))]
    sections = [loc.section_path for loc in locations if loc.section_path]
    lines = [(loc.start_line, loc.end_line) for loc in locations if loc.start_line]
    paragraphs = [loc.paragraph_index for loc in locations if loc.paragraph_index]
    return {
        "start_page": min(pages) if pages else None, "end_page": max(pages) if pages else None,
        "section_path": sections[0] if sections else None,
        "paragraph_index": min(paragraphs) if paragraphs else None,
        "end_paragraph_index": max(paragraphs) if paragraphs else None,
        "start_line": min(a for a, _ in lines) if lines else None,
        "end_line": max(b for _, b in lines) if lines else None,
        "html_element_id": next((loc.html_element_id for loc in locations if loc.html_element_id), None),
        "blocks": [loc.model_dump(exclude_none=True) for loc in locations],
    }


def source_label(location):
    labels = []
    for first, last, label in (("start_page", "end_page", "Стр."),
                               ("paragraph_index", "end_paragraph_index", "Абз."),
                               ("start_line", "end_line", "Строки")):
        if location[first]:
            suffix = f"–{location[last]}" if location[last] != location[first] else ""
            labels.append(f"{label} {location[first]}{suffix}")
    if location["section_path"]:
        labels.append(" / ".join(location["section_path"]))
    if location["html_element_id"]:
        labels.append("#" + location["html_element_id"])
    return " · ".join(labels)


class CitationResolver:
    def __init__(self, documents):
        self.documents = documents

    def resolve(self, points):
        cache, results = {}, []
        for point in points:
            payload = point["payload"]
            doc_id = payload["document_id"]
            if doc_id not in cache:
                try:
                    version = self.documents.raw(doc_id)["active_version"]
                    path = self.documents.files.canonical(doc_id, version) if version else None
                    cache[doc_id] = (version, CanonicalDocument.model_validate_json(path.read_bytes())) if path else None
                except (AppError, ValueError, FileNotFoundError):
                    cache[doc_id] = None
            cached = cache[doc_id]
            if not cached or cached[0] != payload["document_version"]:
                continue
            version, canonical = cached
            start, end = payload["start_offset"], payload["end_offset"]
            if not 0 <= start < end <= len(canonical.canonical_text):
                continue
            text = canonical.canonical_text[start:end]
            if text != payload["lexical_text"]:
                continue
            location = coordinates(source_slice(canonical, start, end))
            prefix = f"/api/v1/documents/{doc_id}"
            preview = None
            if canonical.original_format == "pdf":
                preview = prefix + "/preview"
                if location["start_page"]:
                    preview += f"#page={location['start_page']}"
            elif canonical.preview_file_path:
                suffix = Path(canonical.preview_file_path).suffix[1:]
                if self.documents.files.preview(doc_id, version, suffix).is_file():
                    preview = prefix + "/preview"
                    if suffix == "html" and location["blocks"]:
                        preview += "#" + location["blocks"][0]["block_id"]
            title = canonical.metadata.get("title") or canonical.original_filename
            results.append({
                "source_id": f"S{len(results)+1}", "document_id": doc_id, "document_version": version,
                "chunk_id": payload["chunk_id"], "title": title, "document_title": title,
                "original_filename": canonical.original_filename, "original_format": canonical.original_format,
                "mime_type": canonical.mime_type, "text": text, "original_text": text,
                "document_url": prefix + "/file", "preview_url": preview,
                "content_url": prefix + "/content", "source_location": location,
                "score": point.get("score"), "start_offset": start, "end_offset": end,
            })
        return results

    def current(self, source):
        try:
            return self.documents.raw(source["document_id"])["active_version"] == source["document_version"]
        except AppError:
            return False

    def validate(self, answer, permitted):
        by_id = {source["source_id"]: source for source in permitted}
        found, warnings = {}, []

        def replace(match):
            valid = []
            for source_id in REFERENCE_ID.findall(match[0]):
                source_id = source_id.upper()
                source = by_id.get(source_id)
                if source and self.current(source):
                    found[source_id] = source
                    if source_id not in valid:
                        valid.append(source_id)
                elif "invalid_citations" not in warnings:
                    warnings.append("invalid_citations")
            return "[" + ", ".join(valid) + "]" if valid else ""

        normalized = PROSE_REFERENCE.sub(
            lambda match: f"{match['label']}[{', '.join(REFERENCE_ID.findall(match['ids']))}]",
            answer,
        )
        cleaned = ANSWER_LINK.sub(lambda match: match[1] or "", REFERENCE.sub(replace, normalized)).strip()
        if not found:
            warnings.append("no_citations")
        return cleaned, list(found.values()), warnings


def select_results(points, limit, per_document, diversity):
    primary, deferred = [], []
    counts, content = Counter(), set()
    for point in points:
        payload = point["payload"]
        digest = payload["content_hash"]
        if digest in content:
            continue
        content.add(digest)
        doc_id = payload["document_id"]
        if diversity and counts[doc_id] >= per_document:
            deferred.append(point)
        else:
            primary.append(point)
            counts[doc_id] += 1
        if len(primary) >= limit:
            break
    return (primary + deferred)[:limit]
