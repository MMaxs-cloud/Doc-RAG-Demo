import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from app.config import Config


def uid() -> str:
    return str(uuid4())


class Database:
    def __init__(self, config: Config):
        self.path = config.data / "database" / "app.db"
        self.config = config

    @contextmanager
    def connection(self, *, write=False):
        with self._connect() as db:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute("PRAGMA busy_timeout = 15000")
                db.execute("PRAGMA foreign_keys = ON")
                yield db
        finally:
            db.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("Версия SQLite новее текущего приложения")
            if version == 0:
                script = Path(__file__).with_name("001_initial.sql").read_text(encoding="utf-8")
                db.executescript("BEGIN IMMEDIATE;\n" + script + "\nPRAGMA user_version = 1;\nCOMMIT;")
            db.execute("INSERT OR IGNORE INTO index_versions VALUES (?,?,?)", (
                self.config.index_version,
                json.dumps({"embeddings": self.config.embeddings.model_dump(),
                            "chunking": self.config.chunking.model_dump()}),
                time.time(),
            ))

    def one(self, sql: str, args=()):
        with self.connection() as db:
            row = db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, args=()):
        with self.connection() as db:
            return [dict(row) for row in db.execute(sql, args)]

    def service(self, name: str, detail: str | None = None):
        with self.connection(write=True) as db:
            db.execute(
                "INSERT INTO service_state VALUES (?,?,?) ON CONFLICT(service) DO UPDATE SET "
                "updated_at=excluded.updated_at, detail=excluded.detail", (name, time.time(), detail),
            )
