from threading import Lock

import httpx

from app.config import Config
from app.core.errors import AppError

QDRANT_VERSION = (1, 20)
LEXICAL = {"type": "text", "tokenizer": "word", "lowercase": True, "scoring": {"type": "bm25"}}
INDEXES = {
    "lexical_text": LEXICAL, "document_id": "keyword", "document_version": "keyword",
    "format": "keyword", "publication_year": "integer", "language": "keyword",
}


def condition(key, value):
    return {"key": key, "match": {"value": value}}


class Qdrant:
    def __init__(self, settings: Config):
        self.config = settings
        self.name = f"trag_chunks_{settings.index_version}"
        self.path = f"/collections/{self.name}"
        self.http = httpx.Client(base_url=settings.qdrant_url, timeout=httpx.Timeout(45, connect=3),
                                 trust_env=False)
        self._ensured = False
        self._lock = Lock()

    def request(self, method, path, *, data=None, missing=False):
        try:
            response = self.http.request(method, path, json=data)
            if response.status_code == 404 and missing:
                return None
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise AppError("qdrant_request_failed", f"Qdrant вернул HTTP {status}", 503,
                           retryable=status >= 500 or status == 429) from None
        except (httpx.HTTPError, ValueError):
            raise AppError("qdrant_unavailable", "Qdrant недоступен", 503, retryable=True) from None

    def ensure(self, *, force=False):
        with self._lock:
            if self._ensured and not force:
                return
            server = self.request("GET", "/")
            try:
                version = tuple(int(part) for part in server["version"].split(".")[:2])
            except (ValueError, KeyError):
                version = ()
            if version < QDRANT_VERSION:
                raise AppError("qdrant_version", "Требуется Qdrant 1.20 или новее для нативного BM25", 503)
            current = self.request("GET", self.path, missing=True)
            if current is None:
                try:
                    self.request("PUT", self.path, data={
                        "vectors": {"dense": {"size": self.config.embeddings.dimension, "distance": "Cosine"}},
                    })
                except AppError:
                    if self.request("GET", self.path, missing=True) is None:
                        raise
                current = self.request("GET", self.path)
            result = current["result"]
            dense = result["config"]["params"]["vectors"].get("dense", {})
            if dense.get("size") != self.config.embeddings.dimension or dense.get("distance") != "Cosine":
                raise AppError("qdrant_schema", "Размерность или метрика коллекции Qdrant несовместимы", 503)
            for name, schema in INDEXES.items():
                existing = result["payload_schema"].get(name)
                if not existing:
                    self.request("PUT", f"{self.path}/index?wait=true", data={
                        "field_name": name, "field_schema": schema,
                    })
                elif name == "lexical_text":
                    params = existing.get("params") or {}
                    if any(params.get(key) != value for key, value in LEXICAL.items() if key != "type"):
                        raise AppError("qdrant_schema", "Полнотекстовое поле настроено без ожидаемого BM25", 503)
                elif existing.get("data_type") != schema:
                    raise AppError("qdrant_schema", "Тип фильтра Qdrant несовместим", 503)
            self._ensured = True

    def upsert(self, points):
        self.request("PUT", f"{self.path}/points?wait=true", data={"points": points})

    def delete(self, field, value):
        self.request("POST", f"{self.path}/points/delete?wait=true", missing=True,
                     data={"filter": {"must": [condition(field, value)]}})

    def version_count(self, version):
        reply = self.request("POST", f"{self.path}/points/count", missing=True, data={
            "exact": True, "filter": {"must": [condition("document_version", version)]},
        })
        return reply["result"]["count"] if reply else 0

    def status(self):
        reply = self.request("GET", self.path, missing=True)
        return reply["result"] if reply else {}

    def query(self, mode, text, vector, filters, limit):
        def leg(using, query, top_k):
            return {"using": using, "query": query, "filter": filters, "limit": max(top_k, limit)}

        cfg = self.config.retrieval
        dense = leg("dense", vector, cfg.dense_top_k)
        lexical = leg("lexical_text", {"text": {"query": text}}, cfg.bm25_top_k)
        if mode == "hybrid":
            body = {"prefetch": [dense, lexical], "query": {"rrf": {"k": cfg.rrf_k}},
                    "filter": filters, "limit": limit}
        else:
            body = {**(dense if mode == "dense" else lexical), "limit": limit}
        body["with_payload"] = True
        return self.request("POST", f"{self.path}/points/query", data=body)["result"]["points"]

    def orphan_candidates(self, offset=None, limit=256):
        reply = self.request("POST", f"{self.path}/points/scroll", missing=True, data={
            "limit": limit, "offset": offset, "with_payload": ["document_version", "document_id"],
            "with_vector": False,
        })
        return reply["result"] if reply else {"points": [], "next_page_offset": None}
