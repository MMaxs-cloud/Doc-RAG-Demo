import re
from pathlib import Path

import pytest

from app.config import config

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
DOCUMENTS = {
    "txt": ("thermal-protocol.txt", "TVC-17"),
    "md": ("controller.md", "THERM-6"),
    "html": ("measurements.html", "NG-4"),
    "docx": ("calibration.docx", "CL-8"),
    "pdf": ("mounting.pdf", "HX-9"),
    "doc": ("power.doc", "SP-32"),
}


@pytest.fixture
def settings(tmp_path):
    return config().model_copy(update={"data": tmp_path, "parser_socket": None})


class TestTokenizer:
    @staticmethod
    def __call__(text, **kwargs):
        return {"offset_mapping": [match.span() for match in re.finditer(r"\S+", text)]}


@pytest.fixture
def vocab():
    return TestTokenizer()
