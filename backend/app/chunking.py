import hashlib
import re
from dataclasses import dataclass
from uuid import UUID, uuid5

from app.core.errors import AppError
from app.parsing.models import CanonicalDocument, SourceLocation


@dataclass(frozen=True)
class Chunk:
    id: str
    index: int
    text: str
    start: int
    end: int
    tokens: int
    content_hash: str
    locations: list[SourceLocation]


def source_slice(document, start: int, end: int, block_index=0):
    locations = []
    for index in range(block_index, len(document.source_blocks)):
        block = document.source_blocks[index]
        if block.start_offset >= end:
            break
        if block.end_offset <= start:
            continue
        a, b = max(start, block.start_offset), min(end, block.end_offset)
        data = block.model_dump(exclude={"text", "block_type", "line_aligned"})
        data.update(start_offset=a, end_offset=b)
        if block.line_aligned:
            local_start, local_end = a - block.start_offset, b - block.start_offset
            data.update(
                source_start=block.source_start + local_start,
                source_end=block.source_start + local_end,
                start_line=block.start_line + len(re.findall(r"\r\n|\r|\n", block.text[:local_start])),
                end_line=block.start_line + len(re.findall(r"\r\n|\r|\n", block.text[:max(0, local_end - 1)])),
            )
            data["end_line"] = max(data["start_line"], data["end_line"])
        locations.append(SourceLocation(**data))
    return locations


def make_chunks(document: CanonicalDocument, tokenizer, version: str, size=512, overlap=80):
    text = document.canonical_text
    offsets = tokenizer(text, return_offsets_mapping=True, add_special_tokens=False,
                        truncation=False, verbose=False)["offset_mapping"]
    if not offsets:
        raise AppError("no_tokens", "Текст документа не содержит токенов для поиска")
    block_index = 0
    namespace = UUID(version)
    for index, first in enumerate(range(0, len(offsets), size - overlap)):
        last = min(first + size, len(offsets))
        start = offsets[first][0] if first else 0
        end = max(offsets[last - 1][1], offsets[last][0]) if last < len(offsets) else len(text)
        while block_index < len(document.source_blocks) and document.source_blocks[block_index].end_offset <= start:
            block_index += 1
        fragment = text[start:end]
        if not fragment.strip():
            raise AppError("empty_chunk", "Токенизатор вернул пустой фрагмент")
        yield Chunk(str(uuid5(namespace, str(index))), index, fragment, start, end, last - first,
                    hashlib.sha256(fragment.encode()).hexdigest(), source_slice(document, start, end, block_index))
        if last == len(offsets):
            break
