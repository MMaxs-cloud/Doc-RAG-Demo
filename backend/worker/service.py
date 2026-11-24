import hashlib
import json
import shutil
from uuid import UUID
import threading
import time
from pathlib import Path

from app.chunking import make_chunks
from app.config import Config, config
from app.core.errors import AppError
from app.core.logging import configure, event
from app.embeddings import EmbeddingHTTP, tokenizer
from app.jobs import Cancelled, Jobs, LostLease
from app.parsing.models import CanonicalDocument
from app.parsing.runner import office_status, parse_document
from app.retrieval.qdrant import Qdrant
from app.storage.database import Database
from app.storage.files import Files, atomic_write


class Worker:
    def __init__(self, config: Config, *, index=None, embeddings=None, tokenization=None, parsing=None):
        self.config = config
        self.db, self.files = Database(config), Files(config)
        self.jobs = Jobs(self.db)
        self.index = index or Qdrant(config)
        self.embeddings = embeddings or EmbeddingHTTP(config)
        self.tokenization = tokenization
        self.parsing = parsing or parse_document
        self.db.initialize()

    def parse(self, doc, version):
        signature = hashlib.sha256(json.dumps([
            "parsers-1", self.config.parser_signature, doc["encoding_hint"],
        ]).encode()).hexdigest()[:20]
        previous = doc["active_version"]
        existing = self.db.one("SELECT parser_signature FROM document_versions WHERE id=?", (previous,))
        if existing and existing["parser_signature"] == signature:
            try:
                canonical = CanonicalDocument.model_validate_json(self.files.canonical(doc["id"], previous).read_bytes())
                needs_refresh = (doc["format"] in ("doc", "docx") and not canonical.preview_file_path and
                                 (self.config.allow_native_office or self.config.parser_socket)) or (
                    doc["format"] == "pdf" and any(w.startswith("pdf_fallback:") for w in canonical.warnings) and
                    (self.config.docling_artifacts or self.config.parser_socket)
                )
                if needs_refresh:
                    return self.parsing(self.config, self.files, doc, version), signature
                if canonical.preview_file_path:
                    suffix = Path(canonical.preview_file_path).suffix[1:]
                    raw = self.files.preview(doc["id"], previous, suffix).read_bytes()
                    path = self.files.preview(doc["id"], version, suffix)
                    atomic_write(path, raw)
                    canonical.preview_file_path = path.relative_to(self.config.data).as_posix()
                canonical.updated_at = time.time()
                return canonical, signature
            except FileNotFoundError:
                pass
        return self.parsing(self.config, self.files, doc, version), signature

    def process(self, job):
        document_id, version = job["document_id"], job["claim"]
        doc = self.db.one("SELECT * FROM documents WHERE id=?", (document_id,))
        started = time.perf_counter()
        if job["kind"] == "delete":
            self.jobs.stage(job, "indexing", 40)
            versions = self.db.all("SELECT DISTINCT index_version FROM document_versions WHERE document_id=?", (document_id,))
            targets = {row["index_version"] for row in versions}
            targets.add(self.config.index_version)
            for target in targets:
                if target == self.config.index_version:
                    self.index.delete("document_id", document_id)
                else:
                    self.index.request("POST", f"/collections/trag_chunks_{target}/points/delete?wait=true",
                                       missing=True, data={"filter": {"must": [
                                           {"key": "document_id", "match": {"value": document_id}},
                                       ]}})
            self.files.remove_document(document_id)
            with self.db.connection(write=True) as connection:
                self.jobs.owned(connection, job)
                connection.execute("DELETE FROM documents WHERE id=?", (document_id,))
            event("document_deleted", document_id=document_id, job_id=job["id"])
            return
        if doc["deleted_at"] is not None:
            raise Cancelled
        self.jobs.stage(job, "conversion" if doc["format"] == "doc" else "parsing", 10)
        canonical, signature = self.parse(doc, version)
        parsed = time.perf_counter()
        self.jobs.stage(job, "canonicalization", 35)
        atomic_write(self.files.canonical(document_id, version), canonical.model_dump_json().encode())
        self.jobs.stage(job, "chunking", 40)
        cfg = self.config.embeddings
        vocab = self.tokenization or tokenizer(cfg.model, cfg.revision,
                                               str(self.config.embed_path) if self.config.embed_path else None)
        options = self.config.chunking
        chunks = list(make_chunks(canonical, vocab, version, options.size, options.overlap))
        self.index.ensure()
        title = canonical.metadata.get("title") or doc["filename"]
        embedding_seconds = indexing_seconds = 0.0
        for offset in range(0, len(chunks), cfg.batch_size):
            batch = chunks[offset:offset+cfg.batch_size]
            self.jobs.stage(job, "embedding", 45 + int(48 * offset / len(chunks)))
            before = time.perf_counter()
            vectors = self.embeddings.embed_documents([chunk.text for chunk in batch])
            embedding_seconds += time.perf_counter() - before
            if len(vectors) != len(batch) or any(len(vector) != cfg.dimension for vector in vectors):
                raise AppError("bad_embedding", "Неверная размерность или число векторов")
            self.jobs.stage(job, "indexing", 46 + int(48 * offset / len(chunks)))
            points = []
            for chunk, vector in zip(batch, vectors, strict=True):
                points.append({"id": chunk.id, "vector": {"dense": vector}, "payload": {
                    "chunk_id": chunk.id, "document_id": document_id, "document_version": version,
                    "index_version": self.config.index_version, "chunk_index": chunk.index,
                    "title": title, "format": doc["format"], "start_offset": chunk.start, "end_offset": chunk.end,
                    "token_count": chunk.tokens, "content_hash": chunk.content_hash,
                    "lexical_text": chunk.text, "metadata": canonical.metadata,
                    "source_locations": [loc.model_dump(exclude_none=True) for loc in chunk.locations],
                    "publication_year": canonical.metadata.get("publication_year"),
                    "language": canonical.metadata.get("language"),
                }})
            before = time.perf_counter()
            self.index.upsert(points)
            indexing_seconds += time.perf_counter() - before
        self.jobs.stage(job, "finalizing", 97)
        old = self.jobs.publish(job, len(chunks), canonical.metadata, signature)
        if old:
            try:
                self.prune(old)
            except (AppError, OSError):
                event("retired_version_cleanup_deferred", document_id=document_id, document_version=old)
        event("document_indexed", document_id=document_id, job_id=job["id"], format=doc["format"],
              chars=len(canonical.canonical_text), chunks=len(chunks),
              parse_seconds=round(parsed-started, 4), embedding_seconds=round(embedding_seconds, 4),
              indexing_seconds=round(indexing_seconds, 4), seconds=round(time.perf_counter()-started, 4))

    def prune(self, version):
        retired = self.db.one(
            "SELECT v.* FROM document_versions v JOIN documents d ON d.id=v.document_id "
            "WHERE v.id=? AND (d.active_version IS NULL OR d.active_version<>v.id)", (version,),
        )
        if not retired:
            return
        if retired["index_version"] == self.config.index_version:
            self.index.delete("document_version", version)
        else:
            path = f"/collections/trag_chunks_{retired['index_version']}/points/delete?wait=true"
            self.index.request("POST", path, missing=True, data={
                "filter": {"must": [{"key": "document_version", "match": {"value": version}}]},
            })
        self.files.remove_version(retired["document_id"], version)
        with self.db.connection(write=True) as db:
            db.execute("DELETE FROM document_versions WHERE id=?", (version,))

    def cleanup_candidate(self, job):
        if job["kind"] != "index":
            return
        version = job["claim"]
        if self.db.one("SELECT 1 FROM document_versions WHERE id=?", (version,)):
            return
        self.files.remove_version(job["document_id"], version)
        self.index.delete("document_version", version)

    def reconcile(self):
        self.index.ensure(force=True)
        active = self.db.all(
            "SELECT d.id,v.id AS version,v.chunk_count FROM documents d JOIN document_versions v ON v.id=d.active_version "
            "WHERE d.deleted_at IS NULL AND v.index_version=?", (self.config.index_version,),
        )
        for row in active:
            available = self.index.version_count(row["version"]) == row["chunk_count"]
            if available and self.files.canonical(row["id"], row["version"]).is_file():
                continue
            with self.db.connection(write=True) as db:
                current = db.execute("SELECT active_version,deleted_at FROM documents WHERE id=?", (row["id"],)).fetchone()
                if not current or current["active_version"] != row["version"] or current["deleted_at"] is not None:
                    continue
                db.execute("UPDATE documents SET active_version=NULL WHERE id=?", (row["id"],))
                if not db.execute(
                    "SELECT 1 FROM indexing_jobs WHERE document_id=? AND kind='index' AND status IN ('pending','processing')",
                    (row["id"],),
                ).fetchone():
                    self.jobs.enqueue(db, row["id"])
        retired = self.db.all("SELECT v.id FROM document_versions v JOIN documents d ON d.id=v.document_id "
                              "WHERE d.active_version IS NULL OR v.id <> d.active_version")
        for row in retired:
            self.prune(row["id"])
        cursor = None
        scanned = set()
        while True:
            page = self.index.orphan_candidates(cursor)
            for point in page["points"]:
                version = point["payload"].get("document_version")
                if not version or version in scanned:
                    continue
                scanned.add(version)
                owned = self.db.one("SELECT 1 FROM document_versions WHERE id=? UNION ALL "
                                    "SELECT 1 FROM indexing_jobs WHERE claim=? AND status='processing'", (version, version))
                if not owned:
                    self.index.delete("document_version", version)
            cursor = page.get("next_page_offset")
            if cursor is None:
                break
        self.clean_abandoned_files()

    def clean_abandoned_files(self):
        cutoff = time.time() - max(3600, self.config.parsing.timeout_seconds * 2)
        for directory in (self.config.data / "documents", self.files.temporary):
            if not directory.is_dir():
                continue
            for item in directory.iterdir():
                try:
                    if item.is_symlink() or item.stat().st_mtime >= cutoff:
                        continue
                    if directory == self.files.temporary:
                        if self.db.one("SELECT 1 FROM indexing_jobs WHERE claim=? AND status='processing'", (item.name,)):
                            continue
                        if item.is_dir():
                            shutil.rmtree(item)
                        else:
                            item.unlink(missing_ok=True)
                    elif item.is_dir() and not self.db.one("SELECT 1 FROM documents WHERE id=?", (item.name,)):
                        try:
                            document_id = str(UUID(item.name))
                        except ValueError:
                            continue
                        if document_id == item.name:
                            self.files.remove_document(document_id)
                except FileNotFoundError:
                    continue
        keep = {row["id"] for row in self.db.all(
            "SELECT id FROM document_versions UNION SELECT claim FROM indexing_jobs WHERE status='processing'"
        )}
        for folder in ("canonical", "previews"):
            root = self.config.data / folder
            if root.is_dir():
                for file in root.glob("*/*"):
                    try:
                        if file.is_file() and not file.is_symlink() and file.stem not in keep and file.stat().st_mtime < cutoff:
                            file.unlink(missing_ok=True)
                    except FileNotFoundError:
                        continue

    def run_once(self):
        self.db.service("worker")
        job = self.jobs.claim()
        if not job:
            return False
        stop = threading.Event()

        def heartbeat():
            while not stop.wait(self.config.jobs.lease_seconds / 3):
                self.jobs.heartbeat(job)
                self.db.service("worker")

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            self.process(job)
        except (Cancelled, LostLease) as exc:
            if isinstance(exc, Cancelled):
                self.jobs.reject(job, "cancelled")
        except AppError as exc:
            status = "requires_ocr" if exc.code == "requires_ocr" else "failed"
            if exc.retryable and job["attempts"] < self.config.jobs.attempts:
                status = "pending"
            self.jobs.reject(job, status, exc)
            event("index_job_error", document_id=job["document_id"], job_id=job["id"], error_code=exc.code)
            if status == "pending":
                time.sleep(min(2 ** job["attempts"], 15))
        except Exception as exc:
            self.jobs.reject(job, "failed", AppError("worker_error", "Ошибка обработки. Проверьте состояние сервисов."))
            event("worker_error", document_id=job["document_id"], job_id=job["id"], error_type=type(exc).__name__)
        finally:
            stop.set()
            thread.join()
            try:
                self.cleanup_candidate(job)
            except (AppError, OSError):
                event("candidate_cleanup_deferred", document_id=job["document_id"], job_id=job["id"])
        return True


def main():
    configure()
    worker = Worker(config())
    worker.db.service('worker')
    next_reconcile = 0.0
    while True:
        if time.monotonic() >= next_reconcile:
            worker.db.service("libreoffice", office_status(worker.config))
            try:
                worker.reconcile()
                next_reconcile = time.monotonic() + 120
            except (AppError, OSError) as exc:
                next_reconcile = time.monotonic() + 15
                event("reconcile_deferred", error_code=getattr(exc, "code", type(exc).__name__))
        if not worker.run_once():
            time.sleep(1)


if __name__ == "__main__":
    main()
