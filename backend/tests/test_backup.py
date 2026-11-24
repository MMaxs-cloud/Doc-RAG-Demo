import importlib.util
from pathlib import Path

import pytest

from app.storage.database import Database


module_path = Path(__file__).resolve().parents[2] / "scripts" / "storage.py"
spec = importlib.util.spec_from_file_location("backup", module_path)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


def test_offline_bundle_roundtrip(settings, tmp_path):
    app = tmp_path / "application"
    qdrant = tmp_path / "qdrant"
    db = Database(settings.model_copy(update={"data": app}))
    db.initialize()
    with db.connection(write=True) as connection:
        connection.execute("INSERT INTO app_settings VALUES ('mode', '\"bm25\"')")
    qdrant.mkdir()
    (qdrant / "test_data").write_bytes(b"payload")
    original = app / "documents" / "id" / "original"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"test document")
    bundle = tmp_path / "share.zip"
    backup.backup(app, qdrant, bundle)
    restored = tmp_path / "restored"
    backup.restore(bundle, restored)
    restored_db = Database(settings.model_copy(update={"data": restored / "application"}))
    assert restored_db.one("SELECT * FROM app_settings")["value"] == '"bm25"'
    assert (restored / original.relative_to(tmp_path)).read_bytes() == original.read_bytes()
    assert (restored / "qdrant" / "test_data").read_bytes() == b"payload"
    with pytest.raises(FileExistsError):
        backup.restore(bundle, restored)
    for path in ("../qdrant/evil", "application/database/../foo", "application/C:/bad", "qdrant/../outside"):
        with pytest.raises(ValueError):
            backup.validate_name(path)
