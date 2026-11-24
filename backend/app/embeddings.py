from functools import lru_cache
from typing import Protocol

import httpx

from app.config import Config
from app.core.errors import AppError


class EmbeddingProvider(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, query: str) -> list[float]: ...
    def get_model_info(self) -> dict: ...


@lru_cache(maxsize=4)
def tokenizer(model: str, revision: str, local: str | None):
    from transformers import AutoTokenizer

    result = AutoTokenizer.from_pretrained(local or model, revision=revision, use_fast=True,
                                           local_files_only=True, trust_remote_code=False)
    if not result.is_fast:
        raise RuntimeError("Fast tokenizer is required for source offsets")
    return result


class EmbeddingHTTP:
    def __init__(self, settings: Config):
        self.settings = settings
        self.http = httpx.Client(base_url=settings.embed_url, timeout=httpx.Timeout(180, connect=3),
                                 trust_env=False)

    def _request(self, path, body=None):
        try:
            response = self.http.get(path) if body is None else self.http.post(path, json=body)
            response.raise_for_status()
            result = response.json()
            if result["index_version"] != self.settings.index_version:
                raise AppError("embedding_version", "Сервис embeddings использует несовместимую конфигурацию", 503)
            return result
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 422:
                raise AppError("embedding_input", "Вход модели embeddings превышает лимит или не содержит текста", 422) from None
            raise AppError("embedding_unavailable", "Сервис embeddings не смог обработать запрос", 503,
                           retryable=True) from None
        except (httpx.HTTPError, ValueError, KeyError):
            raise AppError("embedding_unavailable", "Сервис embeddings недоступен", 503, retryable=True) from None

    def get_model_info(self):
        return self._request("/health")

    def embed_documents(self, texts):
        return self._request("/embed", {"texts": texts, "query": False})["vectors"]

    def embed_query(self, query):
        return self._request("/embed", {"texts": [query], "query": True})["vectors"][0]
