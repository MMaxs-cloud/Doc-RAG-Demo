import time

from app.citations import select_results
from app.core.logging import event


class Retrieval:
    def __init__(self, config, documents, qdrant, embeddings, citations):
        self.config, self.documents, self.qdrant = config, documents, qdrant
        self.embeddings, self.citations = embeddings, citations

    def search(self, query, mode, limit, filters):
        started = time.perf_counter()
        allowed_ids = {str(item) for item in filters.document_ids or []}
        versions = [row["active_version"] for row in self.documents.active()
                    if not allowed_ids or row["id"] in allowed_ids]
        if not versions:
            return []
        must = [{"key": "document_version", "match": {"any": versions}}]
        for field, value in (("format", filters.format), ("publication_year", filters.publication_year),
                             ("language", filters.language)):
            if value is not None:
                must.append({"key": field, "match": {"value": value}})
        self.qdrant.ensure()
        vector = self.embeddings.embed_query(query) if mode in ("dense", "hybrid") else None
        cfg = self.config.retrieval
        count = max(limit, cfg.dense_top_k if mode != "bm25" else 0, cfg.bm25_top_k if mode != "dense" else 0)
        points = self.qdrant.query(mode, query, vector, {"must": must}, count)
        results = self.citations.resolve(select_results(points, limit, cfg.max_chunks_per_document, cfg.diversity_enabled))
        event("retrieval_done", mode=mode, candidates=len(points), results=len(results),
              seconds=round(time.perf_counter() - started, 4))
        return results
