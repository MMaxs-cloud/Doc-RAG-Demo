import asyncio
import time
from uuid import UUID

import httpx
import pytest

from app.chats import GenerationOptions
from app.citations import CitationResolver
from app.core.errors import AppError
from app.generation.context import build_context, uncovered
from app.generation.providers import CloudProvider


def test_cloud_retry_stream_quota_and_refresh(settings):
    async def run():
        cloud = CloudProvider(settings.model_copy(update={"gigachat_credentials": "fixture",
                                                          "gigachat_model": "GigaChat"}))
        await cloud.auth_http.aclose()
        await cloud.http.aclose()
        auth_requests, chat_requests = [], []

        def oauth(request):
            assert request.url.host == "ngw.devices.sberbank.ru"
            UUID(request.headers["rquid"])
            assert request.headers["authorization"] == "Basic fixture"
            assert b"scope=GIGACHAT_API_PERS" in request.content
            auth_requests.append(request)
            return httpx.Response(200, json={"access_token": str(len(auth_requests)),
                                             "expires_at": int((time.time() + 1800) * 1000)})

        def chat(request):
            chat_requests.append(request)
            if len(chat_requests) == 1:
                return httpx.Response(401)
            if len(chat_requests) == 3:
                return httpx.Response(429)
            assert request.headers["authorization"] == "Bearer 2"
            return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"evidence [S1]"}}]}\n\ndata: [DONE]\n\n')

        cloud.auth_http = httpx.AsyncClient(transport=httpx.MockTransport(oauth))
        cloud.http = httpx.AsyncClient(base_url="https://api.giga.chat", transport=httpx.MockTransport(chat))
        options = GenerationOptions(0.1, 100, 1000)
        try:
            assert await cloud.generate([{"role": "user", "content": "synthetic question"}], options) == "evidence [S1]"
            assert len(auth_requests) == 2
            with pytest.raises(AppError) as quota:
                await cloud.generate([{"role": "user", "content": "synthetic question"}], options)
            assert quota.value.code == "generation_quota"
            assert len(auth_requests) == 2
        finally:
            await cloud.close()

    asyncio.run(run())


def test_prose_citations_are_verified_without_converting_sensor_names(monkeypatch):
    resolver = CitationResolver(None)
    monkeypatch.setattr(resolver, "current", lambda source: source["source_id"] != "S2")
    sources = [{"source_id": source_id} for source_id in ["S1", "S2", "S3"]]
    answer, cited, warnings = resolver.validate(
        "Датчик S86. По данным из источника S1 и S2. See source S3 and S999.", sources
    )
    assert answer == "Датчик S86. По данным из источника [S1]. See source [S3]."
    assert [source["source_id"] for source in cited] == ["S1", "S3"]
    assert warnings == ["invalid_citations"]


def test_context_does_not_repeat_overlapping_text():
    assert uncovered(30, 90, [(0, 50), (65, 73)]) == [(50, 65), (73, 90)]
    safe_source = {
        "start_offset": 0, "end_offset": 100, "text": "t" * 100,
        "source_location": dict.fromkeys(("start_page", "paragraph_index", "start_line", "section_path",
                                          "html_element_id")), "source_id": "S1", "title": "A",
        "document_id": "a", "document_version": "v",
    }

    class Tokens:
        window = 8192

        async def count(self, messages):
            return sum(len(message["content"]) for message in messages)

    second = {**safe_source, "source_id": "S2", "start_offset": 90, "end_offset": 190, "text": "u" * 100}
    messages, chosen = asyncio.run(build_context(Tokens(), "question", [], [safe_source, second],
                                                GenerationOptions(0.1, 100, 2000), 2))
    assert chosen[1]["context_ranges"] == [(100, 190)]
    assert chosen[1]["context_text"] == "u" * 90
    assert "u" * 100 not in messages[-1]["content"]
