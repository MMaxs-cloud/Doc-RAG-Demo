import os
import shutil
from pathlib import Path
from uuid import UUID, uuid4

from app.config import Config


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class Files:
    def __init__(self, config: Config):
        self.root = config.data
        self.temporary = self.root / "temporary"
        self.temporary.mkdir(parents=True, exist_ok=True)

    def original(self, document: str) -> Path:
        return self.root / "documents" / str(UUID(document)) / "original"

    def canonical(self, document: str, version: str) -> Path:
        return self.root / "canonical" / str(UUID(document)) / f"{UUID(version)}.json"

    def preview(self, document: str, version: str, suffix: str) -> Path:
        if suffix not in ("pdf", "html"):
            raise ValueError("Недопустимый тип предпросмотра")
        return self.root / "previews" / str(UUID(document)) / f"{UUID(version)}.{suffix}"

    def remove_version(self, document: str, version: str):
        for path in (self.canonical(document, version),
                     self.preview(document, version, "pdf"), self.preview(document, version, "html")):
            path.unlink(missing_ok=True)

    def remove_document(self, document: str):
        for path in (self.original(document).parent, self.canonical(document, document).parent,
                     self.preview(document, document, "pdf").parent):
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                pass
