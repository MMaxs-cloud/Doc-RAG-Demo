import hashlib
import json
import sqlite3
import tempfile
import time
from pathlib import Path

from app.core.errors import AppError
from app.core.logging import event
from app.jobs import Jobs
from app.parsing.detector import DocumentFormatDetector
from app.parsing.models import CanonicalDocument
from app.storage.database import Database, uid
from app.storage.files import Files

DETAIL = """SELECT d.*,v.chunk_count,v.index_version,
  (SELECT j.id FROM indexing_jobs j WHERE j.document_id=d.id ORDER BY j.created_at DESC LIMIT 1) AS job_id
  FROM documents d LEFT JOIN document_versions v ON v.id=d.active_version"""


def document_view(row):
    output = {k: row[k] for k in ("id", "filename", "format", "mime", "sha256", "size", "status",
                                   "active_version", "created_at", "updated_at", "chunk_count",
                                   "index_version", "job_id")}
    output["metadata"] = json.loads(row["metadata"])
    output["title"] = output["metadata"].get("title") or row["filename"]
    return output


class Documents:
    def __init__(self, db: Database, files: Files, jobs: Jobs):
        self.db, self.files, self.jobs = db, files, jobs
        self.limit = db.config.documents.max_file_size_mb * 1024**2
        self.detector = DocumentFormatDetector(db.config.documents.max_docx_uncompressed_mb * 1024**2)

    def upload(self, upload, hint=None):
        digest = hashlib.sha256()
        size = 0
        started = time.perf_counter()
        with tempfile.NamedTemporaryFile(dir=self.files.temporary, delete=False) as temp:
            path = Path(temp.name)
            try:
                while block := upload.file.read(1024**2):
                    size += len(block)
                    if size > self.limit:
                        raise AppError("file_too_large", "Превышен максимальный размер файла", 413)
                    digest.update(block)
                    temp.write(block)
            except BaseException:
                temp.close()
                path.unlink(missing_ok=True)
                raise
        try:
            info = self.detector.detect(path, upload.filename, upload.content_type)
            checksum = digest.hexdigest()
            now = time.time()
            doc_id = uid()
            original = self.files.original(doc_id)
            duplicate = False
            try:
                with self.db.connection(write=True) as db:
                    previous = db.execute("SELECT d.id,d.deleted_at,v.index_version FROM documents d LEFT JOIN document_versions v ON v.id=d.active_version WHERE d.sha256=?", (checksum,)).fetchone()
                    if previous and previous["deleted_at"] is not None:
                        raise AppError("deletion_pending", "Дождитесь завершения удаления идентичного файла", 409)
                    if previous:
                        doc_id, duplicate = previous["id"], True
                        if previous["index_version"] and previous["index_version"] != self.db.config.index_version:
                            if not db.execute(
                                "SELECT 1 FROM indexing_jobs WHERE document_id=? AND kind='index' AND status IN ('pending','processing')",
                                (doc_id,),
                            ).fetchone():
                                self.jobs.enqueue(db, doc_id)
                    else:
                        original.parent.mkdir(parents=True, exist_ok=False)
                        path.replace(original)
                        db.execute(
                            "INSERT INTO documents(id,filename,format,mime,sha256,size,encoding_hint,status,created_at,updated_at) "
                            "VALUES (?,?,?,?,?,?,?,'pending',?,?)",
                            (doc_id, info.filename, info.format, info.mime, checksum, size, hint, now, now),
                        )
                        self.jobs.enqueue(db, doc_id)
            except (sqlite3.Error, OSError, AppError):
                if original.exists():
                    self.files.remove_document(doc_id)
                raise
            event("document_uploaded", document_id=doc_id, format=info.format, size=size,
                  duplicate=duplicate, seconds=round(time.perf_counter() - started, 4))
            return {**self.get(doc_id), "duplicate": duplicate}
        finally:
            path.unlink(missing_ok=True)

    def raw(self, document_id):
        row = self.db.one(DETAIL + " WHERE d.id=?", (document_id,))
        if not row or row["deleted_at"] is not None:
            raise AppError("document_unavailable", "Документ удалён или недоступен", 404)
        return row

    def get(self, document_id):
        view = document_view(self.raw(document_id))
        view["job"] = self.db.one("SELECT id,status,stage,progress,error_code,error_message FROM indexing_jobs WHERE id=?",
                                  (view["job_id"],))
        return view

    def list(self, query, file_format, status, offset, limit):
        where = ["d.deleted_at IS NULL"]
        args = []
        if query:
            # SQLite lower()/NOCASE only fold ASCII. Normalize in the registered SQL function.
            where.append("(instr(casefold(d.filename), ?) > 0 OR instr(casefold(COALESCE(json_extract(d.metadata,'$.title'),'')), ?) > 0)")
            args.extend([query.casefold()] * 2)
        if file_format:
            where.append("d.format=?")
            args.append(file_format)
        if status:
            where.append("d.status=?")
            args.append(status)
        condition = " WHERE " + " AND ".join(where)
        with self.db.connection() as db:
            db.create_function("casefold", 1, str.casefold, deterministic=True)
            rows = db.execute(DETAIL + condition + " ORDER BY d.created_at DESC LIMIT ? OFFSET ?",
                              [*args, limit, offset])
            items = []
            for row in rows:
                item = document_view(dict(row))
                job = db.execute("SELECT id,kind,status,stage,progress,error_code,error_message FROM indexing_jobs WHERE id=?",
                                 (item["job_id"],)).fetchone()
                item["job"] = dict(job) if job else None
                items.append(item)
            total = db.execute("SELECT count(*) FROM documents d" + condition, args).fetchone()[0]
        return {"items": items, "total": total, "offset": offset, "limit": limit}

    def canonical(self, document_id):
        document = self.raw(document_id)
        version = document["active_version"]
        if not version:
            raise AppError("content_unavailable", "Канонический текст ещё недоступен", 404)
        path = self.files.canonical(document_id, version)
        try:
            return CanonicalDocument.model_validate_json(path.read_bytes())
        except FileNotFoundError:
            raise AppError("content_unavailable", "Канонический текст недоступен", 404) from None

    def reindex(self, document_id, encoding=None):
        with self.db.connection(write=True) as db:
            row = db.execute("SELECT id,deleted_at,format FROM documents WHERE id=?", (document_id,)).fetchone()
            if not row or row["deleted_at"] is not None:
                raise AppError("document_unavailable", "Документ недоступен", 404)
            if encoding and row["format"] not in ("txt", "md", "html"):
                raise AppError("encoding_unsupported_format", "Кодировка задаётся для TXT, MD и HTML")
            if encoding:
                db.execute("UPDATE documents SET encoding_hint=? WHERE id=?", (encoding, document_id))
            return self.jobs.enqueue(db, document_id)

    def delete(self, document_id):
        now = time.time()
        with self.db.connection(write=True) as db:
            row = db.execute("SELECT deleted_at FROM documents WHERE id=?", (document_id,)).fetchone()
            if not row or row["deleted_at"] is not None:
                raise AppError("document_unavailable", "Документ недоступен", 404)
            db.execute("UPDATE documents SET deleted_at=?,updated_at=? WHERE id=?", (now, now, document_id))
            db.execute("UPDATE indexing_jobs SET status='cancelled',finished_at=? "
                       "WHERE document_id=? AND kind='index' AND status='pending'", (now, document_id))
            db.execute("UPDATE indexing_jobs SET cancel_requested=1 WHERE document_id=? AND kind='index' AND status='processing'",
                       (document_id,))
            return self.jobs.enqueue(db, document_id, "delete")

    def active(self):
        return self.db.all(
            "SELECT d.id,d.active_version FROM documents d JOIN document_versions v ON v.id=d.active_version "
            "WHERE d.deleted_at IS NULL AND v.index_version=?", (self.db.config.index_version,),
        )
