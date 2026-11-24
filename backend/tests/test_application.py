import time

import pytest
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.main import Services, create_app
from app.parsing.detector import MIME
from worker.service import Worker

from conftest import DOCUMENTS, FIXTURES


class FakeIndex:
    def __init__(self):
        self.points = {}
        self.fail = False

    def ensure(self, **kwargs):
        pass

    def upsert(self, points):
        self.points.update((point["id"], point) for point in points)
        if self.fail:
            raise AppError("index_test_failure", "Искусственный сбой теста")

    def delete(self, field, value):
        self.points = {key: point for key, point in self.points.items() if point["payload"][field] != value}

    def query(self, mode, text, vector, filters, limit):
        result = []
        for point in self.points.values():
            payload = point["payload"]
            if all(payload[item["key"]] in item["match"]["any"] if "any" in item["match"]
                   else payload[item["key"]] == item["match"]["value"] for item in filters["must"]):
                result.append({**point, "score": 1})
        return result[:limit]

    def orphan_candidates(self, offset):
        return {"points": list(self.points.values()), "next_page_offset": None}

    def version_count(self, version):
        return sum(p["payload"]["document_version"] == version for p in self.points.values())


class FakeEmbeddings:
    def embed_documents(self, texts):
        return [[1.0] + [0.0] * 1023 for _ in texts]

    def embed_query(self, query):
        return self.embed_documents([query])[0]


class FakeLLM:
    window = 8192

    async def healthcheck(self):
        return True

    def get_model_info(self):
        return {"model": "Test double"}

    async def count(self, messages):
        return sum(len(message["content"]) for message in messages) // 3

    async def stream(self, messages, options):
        assert "CL-8" in messages[-1]["content"]
        yield "Проверенный текст "
        yield "[S1, S2, S999]."


def upload(client, fmt):
    filename, _ = DOCUMENTS[fmt]
    response = client.post("/api/v1/documents/upload", files={"files": (filename, (FIXTURES / filename).read_bytes(), MIME[fmt])})
    assert response.status_code == 202
    return response.json()["items"][0]["document"]


@pytest.fixture
def runtime(settings, vocab):
    index, embeddings = FakeIndex(), FakeEmbeddings()
    services = Services(settings, index=index, embeddings=embeddings, providers={"local": FakeLLM(), "api": FakeLLM()})
    worker = Worker(settings, index=index, embeddings=embeddings, tokenization=vocab)
    with TestClient(create_app(settings, services=services)) as client:
        yield client, worker, index


def test_full_api_round_trip_multiple_formats_and_generation(runtime):
    client, worker, index = runtime
    docs = [upload(client, fmt) for fmt in ("docx", "md", "pdf", "html", "txt")]
    duplicate = upload(client, "txt")
    assert duplicate["id"] == docs[-1]["id"] and duplicate["duplicate"]
    for doc in docs:
        assert worker.run_once()
        detail = client.get("/api/v1/documents/" + doc["id"]).json()
        assert detail["status"] == "ready", detail
        assert detail["job"]["progress"] == 100
    text = client.post("/api/v1/search", json={"query": "probe", "filters": {"format": "txt"}}).json()["sources"][0]
    assert text["source_location"]["start_line"] == 1
    assert "TVC-17" in text["text"]

    session = client.post("/api/v1/chat/sessions", json={}).json()["id"]
    options = {"query": "Параметры контроллера и калибровка?", "provider": "local", "session_id": session}
    stream = client.post("/api/v1/chat/stream", json=options)
    assert "event: token" in stream.text and "event: final" in stream.text
    history = client.get("/api/v1/chat/sessions/" + session).json()["messages"][0]
    assert "S999" not in history["answer"] and "invalid_citations" in history["warnings"]
    assert len(history["cited"]) == 2 and len(history["retrieved"]) == 5
    for source in history["cited"]:
        canonical = client.get(source["content_url"]).json()
        assert source["text"] == canonical["canonical_text"][source["start_offset"]:source["end_offset"]]
        assert client.get(source["document_url"]).headers["content-disposition"].startswith("attachment")
    answer = client.post("/api/v1/chat", json={**options, "reuse_message_id": history["id"]}).json()
    assert answer["sources"]
    assert client.post("/api/v1/chat", json={**options, "provider": "api"}).status_code == 403
    html = client.get(f"/api/v1/documents/{docs[3]['id']}/preview")
    assert "sandbox" in html.headers["Content-Security-Policy"] and "INACTIVE_PAYLOAD" not in html.text

    doc_id = docs[0]["id"]
    old = client.get(f"/api/v1/documents/{doc_id}").json()["active_version"]
    assert client.post(f"/api/v1/documents/{doc_id}/reindex").status_code == 202
    index.fail = True
    worker.run_once()
    detail = client.get(f"/api/v1/documents/{doc_id}").json()
    assert detail["active_version"] == old and detail["status"] == "failed"
    assert index.version_count(old) == 1
    index.fail = False
    assert client.post("/api/v1/jobs/" + detail["job"]["id"] + "/retry").status_code == 202
    worker.run_once()
    assert index.version_count(old) == 0
    worker.reconcile()

    assert client.delete(f"/api/v1/documents/{doc_id}").status_code == 202
    assert client.get(f"/api/v1/documents/{doc_id}/file").status_code == 404
    assert all(item["document_id"] != doc_id for item in client.post("/api/v1/search", json={"query": "probe"}).json()["sources"])
    worker.run_once()
    assert not worker.files.original(doc_id).exists()
    assert all(p["payload"]["document_id"] != doc_id for p in index.points.values())


def test_worker_lease_cancel_and_reconcile(runtime):
    client, worker, index = runtime
    doc = upload(client, "txt")
    orphan = worker.jobs.claim()
    assert orphan["document_id"] == doc["id"]
    with worker.db.connection(write=True) as db:
        db.execute("UPDATE indexing_jobs SET heartbeat=? WHERE id=?", (time.time() - 1000, orphan["id"]))
    worker.run_once()
    detail = client.get("/api/v1/documents/" + doc["id"]).json()
    assert detail["status"] == "ready"
    assert client.get("/api/v1/jobs/" + orphan["id"]).json()["attempts"] == 2
    index.points.clear()
    worker.reconcile()
    assert client.get("/api/v1/documents/" + doc["id"]).json()["status"] == "pending"
    new = worker.jobs.claim()
    assert client.post("/api/v1/jobs/" + new["id"] + "/cancel").status_code == 202
    with worker.db.connection(write=True) as db:
        db.execute("UPDATE indexing_jobs SET heartbeat=0 WHERE id=?", (new["id"],))
    assert worker.jobs.claim() is None
    assert client.get("/api/v1/jobs/" + new["id"]).json()["status"] == "cancelled"


def test_origin_and_frontend_error_shape(runtime):
    client, _, _ = runtime
    blocked = client.post("/api/v1/search", json={"query": "x"}, headers={"Origin": "https://unrelated.example"})
    assert blocked.status_code == 403
    assert blocked.json()["error"]["request_id"] == blocked.headers["x-request-id"]
    malformed = client.post("/api/v1/search", json={"query": " "})
    assert malformed.status_code == 422
