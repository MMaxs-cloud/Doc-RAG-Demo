import html
import re

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from app.parsing.base import DocumentParser
from app.parsing.models import Extracted
from app.parsing.text import decode_source

DROP = {"script", "style", "template", "noscript", "iframe", "object", "embed", "svg", "canvas",
        "video", "audio", "form", "input", "button"}
HEADINGS = {f"h{i}" for i in range(1, 7)}
BLOCKS = HEADINGS | {"p", "li", "pre", "blockquote", "figcaption", "caption", "tr", "dt", "dd"}
AD = {"advertisement", "ad-banner", "adsbygoogle", "sponsored-ad"}
BLOCK_LABELS = {"li": "list_item", "pre": "code", "blockquote": "quote", "tr": "table_row",
                "figcaption": "caption", "caption": "caption"}


def hidden(tag):
    if tag.name is None:
        return False
    classes = {str(x).lower() for x in tag.get("class", [])}
    return (
        tag.name in DROP or tag.has_attr("hidden") or tag.get("aria-hidden") == "true"
        or (tag.name == "nav" and not tag.find_parent(["main", "article"]))
        or (tag.get("role") == "navigation" and not tag.find_parent(["main", "article"]))
        or bool(classes & AD)
        or bool(re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", tag.get("style", ""), re.I))
    )


def rendered(block):
    escaped = html.escape(block.text)
    tag = {"heading": "h2", "code": "pre", "quote": "blockquote", "caption": "p"}.get(block.block_type, "p")
    return f'<{tag} id="{block.block_id}">{escaped}</{tag}>'


def extract_blocks(root, builder):
    def coordinate(node):
        anchor = node if node.has_attr("id") else node.find_parent(id=True)
        return {"html_element_id": str(anchor["id"]) if anchor is not None else None}

    def walk(node, inherited=None):
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            if node.strip() and inherited is not None:
                inherited.append(str(node))
            return
        if not isinstance(node, Tag) or node.name == "head":
            return

        own = []
        if node.name == "a":
            value = node.get_text(" ", strip=True)
            href = str(node.get("href", ""))
            if href and re.match(r"^(?:https?://|mailto:|[#./])", href, re.I):
                value = f"{value} ({href})"
            if inherited is not None:
                inherited.append(value)
            return
        if node.name == "br":
            if inherited is not None:
                inherited.append("\n")
            return
        if node.name in BLOCKS:
            heading = int(node.name[1]) if node.name in HEADINGS else None
            kind = "heading" if heading else BLOCK_LABELS.get(node.name, "paragraph")
            if node.name == "pre":
                builder.add(node.get_text(), kind, preserve=True, **coordinate(node))
                return
            if node.name == "tr":
                for cell in node.find_all(["th", "td"], recursive=False):
                    cell_parts = []
                    for child in cell.children:
                        walk(child, cell_parts)
                    own.append(" ".join(cell_parts).strip() or cell.get_text(" ", strip=True))
                builder.add(" | ".join(own), kind, **coordinate(node))
                return
            for child in node.children:
                if isinstance(child, Tag) and (child.name in BLOCKS or child.name in {"ul", "ol", "table"}):
                    builder.add(" ".join(own), kind, heading=heading, **coordinate(node))
                    own.clear()
                    walk(child, [])
                else:
                    walk(child, own)
            builder.add(" ".join(own), kind, heading=heading, **coordinate(node))
            return
        sink = inherited if inherited is not None else own
        inline = node.name in {"span", "b", "i", "strong", "em", "code", "small", "sup", "sub", "mark"}
        if inline:
            for child in node.children:
                walk(child, sink)
            return
        for child in node.children:
            if isinstance(child, Tag) and child.name not in {
                "span", "b", "i", "strong", "em", "code", "small", "sup", "sub", "mark", "a", "br",
            }:
                builder.add(" ".join(own), "paragraph", **coordinate(node))
                own.clear()
                walk(child, None)
            elif isinstance(child, NavigableString) and not isinstance(child, Comment):
                if child.strip():
                    own.append(str(child))
            else:
                walk(child, own)
        builder.add(" ".join(own), "paragraph", **coordinate(node))

    walk(root)


class HtmlParser(DocumentParser):
    formats = ("html",)

    def parse(self, path, working, builder, config, encoding):
        if not encoding:
            with path.open("rb") as file:
                head = file.read(4096)
            match = re.search(rb"<meta\b[^>]*\bcharset\s*=\s*[\"']?([\w-]+)", head, re.I)
            encoding = match[1].decode("ascii") if match else None
        text, encoding = decode_source(path, encoding)
        soup = BeautifulSoup(text, "lxml")
        metadata = {}
        if soup.title and soup.title.get_text(strip=True):
            metadata["title"] = soup.title.get_text(" ", strip=True)
        if soup.html and soup.html.get("lang"):
            metadata["language"] = soup.html["lang"]
        fields = {"author": "authors", "citation_author": "authors", "keywords": "keywords",
                  "citation_doi": "doi", "dc.identifier": "doi", "citation_publication_date": "publication_year"}
        for item in soup.find_all("meta"):
            name = fields.get(str(item.get("name", "")).lower())
            value = str(item.get("content", "")).strip()
            if not name or not value:
                continue
            if name in ("authors", "keywords"):
                metadata.setdefault(name, []).append(value)
            elif name == "publication_year":
                year = re.match(r"\d{4}", value)
                if year:
                    metadata[name] = int(year[0])
            elif value.startswith("10."):
                metadata[name] = value
        for tag in soup.find_all(True):
            if hidden(tag):
                tag.decompose()
        extract_blocks(soup.body or soup, builder)
        preview = '<!doctype html><html lang="ru"><meta charset="utf-8"><title>Документ</title><main>'
        preview += "\n".join(rendered(block) for block in builder.blocks) + "</main></html>"
        (working / "preview.html").write_text(preview, encoding="utf-8")
        return Extracted(metadata=metadata, encoding=encoding, preview_format="html")
