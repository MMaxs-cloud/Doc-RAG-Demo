import json
import time

from app.core.errors import AppError
from app.storage.database import Database, uid


class Cancelled(Exception):
    pass


class LostLease(Exception):
    pass


class Jobs:
    def __init__(self, db: Database):
        self.db = db
        self.options = db.config.jobs

    def enqueue(self, connection, document_id, kind="index"):
        job_id = uid()
        try:
            connection.execute(
                "INSERT INTO indexing_jobs(id,document_id,kind,status,stage,created_at) VALUES (?,?,?,'pending','validation',?)",
                (job_id, document_id, kind, time.time()),
            )
        except Exception as exc:
            import sqlite3
            if isinstance(exc, sqlite3.IntegrityError) and connection.execute(
                "SELECT 1 FROM indexing_jobs WHERE document_id=? AND kind=? AND status IN ('pending','processing')",
                (document_id, kind),
            ).fetchone():
                raise AppError("job_exists", "Задание для документа уже выполняется", 409) from None
            raise
        if kind == "index":
            connection.execute("UPDATE documents SET status='pending', updated_at=? WHERE id=?",
                               (time.time(), document_id))
        return job_id

    def recover(self, connection, now: float):
        stale = connection.execute(
            "SELECT * FROM indexing_jobs WHERE status='processing' AND heartbeat<?",
            (now - self.options.lease_seconds,),
        ).fetchall()
        for job in stale:
            if job["cancel_requested"]:
                status, code = "cancelled", "cancelled"
            elif job["attempts"] >= self.options.attempts:
                status, code = "failed", "worker_lost"
            else:
                status, code = "pending", None
            connection.execute(
                "UPDATE indexing_jobs SET status=?,claim=NULL,heartbeat=NULL,finished_at=?,error_code=?,"
                "error_message=?,progress=0,stage='validation' WHERE id=?",
                (status, None if status == "pending" else now, code,
                 "Истёк срок аренды задания" if code == "worker_lost" else None, job["id"]),
            )
            if job["kind"] == "index":
                connection.execute("UPDATE documents SET status=?,updated_at=? WHERE id=? AND deleted_at IS NULL",
                                   (status, now, job["document_id"]))

    def claim(self):
        now = time.time()
        with self.db.connection(write=True) as connection:
            self.recover(connection, now)
            row = connection.execute(
                "SELECT j.* FROM indexing_jobs j JOIN documents d ON d.id=j.document_id "
                "WHERE j.status='pending' AND ((j.kind='delete' AND d.deleted_at IS NOT NULL) "
                "OR (j.kind='index' AND d.deleted_at IS NULL)) "
                "AND NOT EXISTS (SELECT 1 FROM indexing_jobs x WHERE x.document_id=j.document_id AND x.status='processing') "
                "ORDER BY j.kind,j.created_at LIMIT 1",
            ).fetchone()
            if not row:
                return None
            token = uid()
            connection.execute(
                "UPDATE indexing_jobs SET status='processing', claim=?, started_at=?, finished_at=NULL, heartbeat=?,"
                "attempts=attempts+1, error_code=NULL, error_message=NULL WHERE id=?", (token, now, now, row["id"]),
            )
            if row["kind"] == "index":
                connection.execute("UPDATE documents SET status='processing',updated_at=? WHERE id=?",
                                   (now, row["document_id"]))
            return {**dict(row), "claim": token, "attempts": row["attempts"] + 1}

    def owned(self, connection, job, *, check_cancel=True):
        row = connection.execute("SELECT status,claim,cancel_requested FROM indexing_jobs WHERE id=?",
                                 (job["id"],)).fetchone()
        if not row or row["status"] != "processing" or row["claim"] != job["claim"]:
            raise LostLease
        if check_cancel and row["cancel_requested"]:
            raise Cancelled

    def heartbeat(self, job):
        with self.db.connection(write=True) as connection:
            connection.execute(
                "UPDATE indexing_jobs SET heartbeat=? WHERE id=? AND claim=? AND status='processing'",
                (time.time(), job["id"], job["claim"]),
            )

    def stage(self, job, name, progress):
        with self.db.connection(write=True) as connection:
            self.owned(connection, job)
            connection.execute("UPDATE indexing_jobs SET stage=?,progress=max(progress,?),heartbeat=? WHERE id=?",
                               (name, progress, time.time(), job["id"]))

    def publish(self, job, count, metadata, parser_signature):
        now = time.time()
        with self.db.connection(write=True) as connection:
            self.owned(connection, job)
            current = connection.execute("SELECT active_version,deleted_at FROM documents WHERE id=?",
                                         (job["document_id"],)).fetchone()
            if current["deleted_at"] is not None:
                raise Cancelled
            old = current["active_version"]
            connection.execute("INSERT INTO document_versions VALUES (?,?,?,?,?,?)", (
                job["claim"], job["document_id"], self.db.config.index_version, parser_signature, count, now))
            connection.execute(
                "UPDATE documents SET status='ready',active_version=?,metadata=?,updated_at=? WHERE id=?",
                (job["claim"], json.dumps(metadata, ensure_ascii=False), now, job["document_id"]),
            )
            self.finish(connection, job, "ready", None, None)
        return old

    @staticmethod
    def finish(connection, job, status, code, message):
        now = time.time()
        connection.execute(
            "UPDATE indexing_jobs SET status=?, claim=NULL, finished_at=?, heartbeat=NULL, error_code=?,"
            "error_message=?, progress=CASE WHEN ?='ready' THEN 100 WHEN ?='pending' THEN 0 ELSE progress END "
            "WHERE id=?", (status, None if status == "pending" else now, code, message, status, status, job["id"]),
        )

    def reject(self, job, status, error=None):
        try:
            with self.db.connection(write=True) as connection:
                self.owned(connection, job, check_cancel=False)
                self.finish(connection, job, status, error.code if error else None, error.message if error else None)
                if job["kind"] == "index":
                    connection.execute("UPDATE documents SET status=?,updated_at=? WHERE id=? AND deleted_at IS NULL",
                                       (status, time.time(), job["document_id"]))
        except LostLease:
            return

    def cancel(self, job_id):
        with self.db.connection(write=True) as connection:
            row = connection.execute("SELECT * FROM indexing_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise AppError("job_not_found", "Задание не найдено", 404)
            if row["kind"] == "delete" or row["status"] not in ("pending", "processing"):
                raise AppError("job_not_cancellable", "Задание нельзя отменить в текущем состоянии", 409)
            if row["status"] == "pending":
                self.finish(connection, row, "cancelled", None, None)
                connection.execute("UPDATE documents SET status='cancelled',updated_at=? WHERE id=?",
                                   (time.time(), row["document_id"]))
            else:
                connection.execute("UPDATE indexing_jobs SET cancel_requested=1 WHERE id=?", (job_id,))

    def retry(self, job_id):
        with self.db.connection(write=True) as connection:
            row = connection.execute("SELECT j.*,d.deleted_at FROM indexing_jobs j JOIN documents d ON d.id=j.document_id "
                                     "WHERE j.id=?", (job_id,)).fetchone()
            if not row:
                raise AppError("job_not_found", "Задание не найдено", 404)
            if row["status"] not in ("failed", "requires_ocr", "cancelled") or (
                row["deleted_at"] is not None and row["kind"] != "delete"
            ):
                raise AppError("job_not_retryable", "Задание нельзя повторить", 409)
            return self.enqueue(connection, row["document_id"], row["kind"])
