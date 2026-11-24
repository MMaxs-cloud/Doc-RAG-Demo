from concurrent.futures import ThreadPoolExecutor
import math
import statistics
import os
import time

import pytest
from fastapi.testclient import TestClient

from app.config import config
from app.main import create_app
from app.parsing.detector import MIME
from app.parsing.office import libreoffice
from worker.service import Worker

from conftest import DOCUMENTS, FIXTURES

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.environ.get("TRAG_LIVE_TEST") != "1", reason="Run scripts/run_live.py")]


def test_real_embeddings_qdrant_and_document_lifecycle():
    settings = config()
    worker = Worker(settings)
    started = time.perf_counter()
    on_topic, off_topic = worker.embeddings.embed_documents([
        "Тепловой контур поддерживает 45 градусов.", "A gyroscope measures angular velocity.",
    ])
    query = worker.embeddings.embed_query("Какую температуру поддерживает тепловой контур?")
    cosine = lambda lhs, rhs: sum(a*b for a, b in zip(lhs, rhs, strict=True))
    assert len(query) == 1024 and math.isclose(cosine(query, query), 1.0, abs_tol=1e-5)
    assert cosine(query, on_topic) > cosine(query, off_topic)
    print("real_embed", worker.embeddings.get_model_info(), "seconds", time.perf_counter()-started,
          "cosines", cosine(query, on_topic), cosine(query, off_topic))
    files = {fmt: pair for fmt, pair in DOCUMENTS.items() if fmt != "doc" or (
        (FIXTURES / pair[0]).is_file() and libreoffice(settings) and settings.allow_native_office
    )}
    if "doc" not in files:
        print("DOC NOT VERIFIED: isolated native LibreOffice unavailable")
    with TestClient(create_app(settings)) as client:
        documents = {}
        for fmt, (filename, _) in files.items():
            original = (FIXTURES / filename).read_bytes()
            upload = client.post("/api/v1/documents/upload", files={"files": (filename, original, MIME[fmt])})
            doc = upload.json()["items"][0]["document"]
            documents[fmt] = doc["id"]
            before = time.perf_counter()
            assert worker.run_once()
            detail = client.get(f"/api/v1/documents/{doc['id']}").json()
            assert detail["status"] == "ready", detail
            print("indexed", fmt, detail["chunk_count"], "chunks, seconds", time.perf_counter()-before)
            assert detail["chunk_count"] > 0
            canonical = client.get(f"/api/v1/documents/{doc['id']}/content").json()
            print("canonical", fmt, canonical["parser_name"], canonical["warnings"])
            if fmt == "pdf" and settings.docling_artifacts:
                assert not any(w.startswith("pdf_fallback:") for w in canonical["warnings"]), canonical["warnings"]
                assert any(block["block_type"] == "table" for block in canonical["source_blocks"])
            assert client.get(f"/api/v1/documents/{doc['id']}/file").content == original

        for fmt, (_, code) in files.items():
            for mode in ("dense", "bm25", "hybrid"):
                before = time.perf_counter()
                reply = client.post("/api/v1/search", json={"query": code, "mode": mode, "filters": {"format": fmt}})
                assert reply.status_code == 200, reply.text
                sources = reply.json()["sources"]
                assert sources and code in sources[0]["text"], (fmt, mode, sources)
                assert sources[0]["source_location"]["blocks"]
                print("retrieve", fmt, mode, "ms", 1000*(time.perf_counter()-before))
        mixed_query = "THERM-6 45 °C HX-9 28 W"
        mixed = client.post("/api/v1/search", json={"query": mixed_query}).json()["sources"]
        assert {"md", "pdf"} <= {source["original_format"] for source in mixed}
        def request_time(number):
            then = time.perf_counter()
            fmt = list(files)[number % len(files)]
            symbol = files[fmt][1]
            response = client.post("/api/v1/search", json={"query": symbol, "mode": "hybrid", "filters": {"format": fmt}})
            assert response.status_code == 200 and response.json()["sources"]
            return 1000 * (time.perf_counter() - then)

        with ThreadPoolExecutor(max_workers=4) as concurrent:
            started = time.perf_counter()
            timings = list(concurrent.map(request_time, range(24)))
        print("load", "requests", len(timings), "concurrency", 4,
              "p50_ms", statistics.median(timings), "p95_ms", statistics.quantiles(timings, n=20)[18],
              "requests_per_s", len(timings) / (time.perf_counter()-started))
        print("embedding_resources", worker.embeddings.get_model_info())
        if os.environ.get("TRAG_LIVE_LLM") == "1":
            before = time.perf_counter()
            reply = client.post("/api/v1/chat", json={
                "query": "Какая температурная уставка THERM-6 и какая мощность радиатора HX-9? Укажи оба числа.",
                "provider": "local", "max_output_tokens": 256,
            })
            assert reply.status_code == 200, reply.text
            result = reply.json()
            assert {"md", "pdf"} <= {source["original_format"] for source in result["sources"]}, result
            assert "45" in result["answer"] and "28" in result["answer"], result
            print("real_llm_seconds", time.perf_counter()-before, "cited_formats",
                  [source["original_format"] for source in result["sources"]])
        for fmt, doc_id in documents.items():
            previous = client.get(f"/api/v1/documents/{doc_id}").json()["active_version"]
            assert client.post(f"/api/v1/documents/{doc_id}/reindex").status_code == 202
            worker.run_once()
            latest = client.get(f"/api/v1/documents/{doc_id}").json()["active_version"]
            assert latest != previous and latest
            assert client.delete(f"/api/v1/documents/{doc_id}").status_code == 202
            worker.run_once()
            assert client.get(f"/api/v1/documents/{doc_id}/file").status_code == 404
    worker.reconcile()
    assert worker.index.status()["points_count"] == 0
