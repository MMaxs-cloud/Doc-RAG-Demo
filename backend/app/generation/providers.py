import asyncio
import json
import ssl
import time
from abc import ABC, abstractmethod
from uuid import uuid4

import httpx

from app.core.errors import AppError


def provider_error(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (402, 429):
            return AppError("generation_quota", "Исчерпан лимит генерации. Повторите позже.", 429)
        if status in (401, 403):
            return AppError("provider_unauthorized", "Провайдер отклонил авторизацию", 502)
        return AppError("provider_http_error", f"Провайдер вернул HTTP {status}", 502)
    if isinstance(exc, httpx.TimeoutException):
        return AppError("provider_timeout", "Провайдер не ответил вовремя", 504)
    return AppError("provider_unavailable", "Не удалось связаться с провайдером генерации", 503)


class LLMProvider(ABC):
    def __init__(self, settings, base_url, model, verify=True):
        self.settings, self.model = settings, model
        self.http = httpx.AsyncClient(base_url=base_url.rstrip("/"), verify=verify, trust_env=False,
                                      timeout=httpx.Timeout(settings.generation.timeout_seconds, connect=5))
        self.lock = asyncio.Semaphore(1)

    async def headers(self):
        return {}

    async def healthcheck(self):
        try:
            async with asyncio.timeout(6):
                response = await self.http.get(self.health_path, headers=await self.headers())
                response.raise_for_status()
            return True
        except (AppError, httpx.HTTPError, TimeoutError):
            return False

    @abstractmethod
    async def count(self, messages):
        ...

    async def generate(self, messages, options):
        return "".join([piece async for piece in self.stream(messages, options)])

    async def stream(self, messages, options):
        body = {"model": self.model, "messages": messages, "temperature": options.temperature,
                "max_tokens": options.max_output_tokens, "stream": True}
        try:
            async with self.lock, asyncio.timeout(self.settings.generation.timeout_seconds):
                for attempt in range(2):
                    async with self.http.stream("POST", "/v1/chat/completions", json=body,
                                                headers=await self.headers()) as response:
                        if response.status_code == 401 and attempt == 0 and isinstance(self, CloudProvider):
                            self.expires_at = 0
                            continue
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                return
                            choices = json.loads(data).get("choices") or []
                            if choices:
                                text = choices[0].get("delta", {}).get("content")
                                if isinstance(text, str) and text:
                                    yield text
                        raise AppError("provider_incomplete", "Поток генерации оборвался", 502)
        except (httpx.HTTPError, ValueError) as exc:
            raise provider_error(exc) from None
        except TimeoutError:
            raise AppError("provider_timeout", "Превышено время генерации", 504) from None

    def get_model_info(self):
        return {"model": self.model, "window": self.window}

    async def close(self):
        await self.http.aclose()


class LocalProvider(LLMProvider):
    health_path = "/health"

    def __init__(self, settings):
        super().__init__(settings, settings.local_url, settings.local_model)
        self.window = settings.generation.local_window

    async def count(self, messages):
        try:
            template = await self.http.post("/apply-template", json={"messages": messages})
            template.raise_for_status()
            response = await self.http.post("/tokenize", json={"content": template.json()["prompt"], "add_special": True})
            response.raise_for_status()
            return len(response.json()["tokens"])
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise provider_error(exc) from None


class CloudProvider(LLMProvider):
    health_path = "/v1/models"

    def __init__(self, settings):
        ca = settings.gigachat_ca_file
        self.verify = ssl.create_default_context(cafile=str(ca)) if ca else True
        super().__init__(settings, "https://api.giga.chat", settings.gigachat_model, self.verify)
        self.window = settings.generation.api_window
        self.token, self.expires_at = "", 0.0
        self.auth_lock = asyncio.Lock()
        self.auth_http = httpx.AsyncClient(verify=self.verify, timeout=15, trust_env=False)

    async def close(self):
        await self.auth_http.aclose()
        await super().close()

    async def headers(self):
        if not self.settings.gigachat_credentials or not self.model:
            raise AppError("cloud_not_configured", "Задайте ключ и имя модели GigaChat на backend", 503)
        async with self.auth_lock:
            if time.time() >= self.expires_at - 60:
                try:
                    response = await self.auth_http.post("https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
                        headers={"Authorization": f"Basic {self.settings.gigachat_credentials}",
                                 "RqUID": str(uuid4()), "Accept": "application/json"},
                        data={"scope": self.settings.gigachat_scope})
                    response.raise_for_status()
                    data = response.json()
                    self.token = data["access_token"]
                    expires = float(data["expires_at"])
                    self.expires_at = expires / 1000 if expires > 10**11 else expires
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    raise provider_error(exc) from None
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    async def count(self, messages):
        return sum(len(message["content"].encode()) + 32 for message in messages) + 32
