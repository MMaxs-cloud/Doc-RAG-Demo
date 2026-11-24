from abc import ABC, abstractmethod
from pathlib import Path

from app.config import Config
from app.parsing.models import CanonicalBuilder, Extracted


class DocumentParser(ABC):
    formats: tuple[str, ...]
    version = "1"

    def supports(self, file_info):
        return file_info.format in self.formats

    def get_parser_info(self):
        return type(self).__name__, self.version

    @abstractmethod
    def parse(
        self, path: Path, working: Path, builder: CanonicalBuilder, config: Config, encoding: str | None,
    ) -> Extracted:
        ...
