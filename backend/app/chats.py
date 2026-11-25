import asyncio
import json
import time
from dataclasses import dataclass

from app.core.errors import AppError
from app.core.logging import event
from app.generation.context import build_context, fit_history, messages as chat_messages
from app.storage.database import uid


def page(items, total, offset, limit):
    return {"items": items, "total": total, "offset": offset, "limit": limit}


class Chats:
    def __init__(self, db):
        self.db = db

    def create(self, title):
        session_id, now = uid(), time.time()
        with self.db.connection(write=True) as connection:
            connection.execute("INSERT INTO chat_sessions VALUES (?,?,?,?)", (session_id, title.strip(), now, now))
        return self.get(session_id, 0, 50)

    def list(self, offset, limit):
        return page(self.db.all("SELECT * FROM chat_sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?", (limit, offset)),
                    self.db.one("SELECT count(*) AS count FROM chat_sessions")["count"], offset, limit)

    def session(self, session_id):
        result = self.db.one("SELECT * FROM chat_sessions WHERE id=?", (session_id,))
        if not result:
            raise AppError("session_not_found", "Диалог не найден", 404)
        return result

    def get(self, session_id, offset, limit):
        session = self.session(session_id)
        rows = self.db.all("SELECT * FROM chat_messages WHERE session_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                           (session_id, limit, offset))
        return {**session, "messages": list(reversed([self.decode(row) for row in rows])),
                "total": self.db.one("SELECT count(*) AS count FROM chat_messages WHERE session_id=?", (session_id,))["count"]}

    @staticmethod
    def decode(row):
        return {**row, **{field: json.loads(row[field]) for field in ("retrieved", "cited", "warnings")}}

    def message(self, session_id, message_id):
        row = self.db.one("SELECT * FROM chat_messages WHERE session_id=? AND id=?", (session_id, message_id))
        if not row:
            raise AppError("message_not_found", "Исходный ответ не найден", 404)
        return self.decode(row)

    def recent(self, session_id, before=None):
        if not session_id:
            return []
        self.session(session_id)
        rows = self.db.all("SELECT question,answer FROM chat_messages WHERE session_id=? AND created_at<? "
                           "AND NOT EXISTS (SELECT 1 FROM json_each(chat_messages.warnings) WHERE value='generation_timeout') "
                           "ORDER BY created_at DESC LIMIT 3", (session_id, before or float("inf")))
        messages = []
        for row in reversed(rows):
            messages.extend([{"role": "user", "content": row["question"][:1200]},
                             {"role": "assistant", "content": row["answer"][:1600]}])
        return messages

    def store(self, session, question, answer, provider, retrieved, citations, warnings, message_id):
        if not session:
            return
        now = time.time()
        with self.db.connection(write=True) as db:
            row = db.execute("SELECT title FROM chat_sessions WHERE id=?", (session,)).fetchone()
            if not row:
                return
            payloads = tuple(json.dumps(value, ensure_ascii=False) for value in (retrieved, citations, warnings))
            db.execute("INSERT INTO chat_messages VALUES (?,?,?,?,?,?,?,?,?)",
                       (message_id, session, question, answer, provider, *payloads, now))
            title = question[:80] if row["title"] == "Новый диалог" else row["title"]
            db.execute("UPDATE chat_sessions SET title=?, updated_at=? WHERE id=?", (title, now, session))

    def delete(self, session):
        with self.db.connection(write=True) as db:
            if not db.execute("DELETE FROM chat_sessions WHERE id=?", (session,)).rowcount:
                raise AppError("session_not_found", "Диалог не найден", 404)


class UserSettings:
    def __init__(self, db, config):
        self.db = db
        self.defaults = {"provider": "local", "mode": "hybrid", "top_k": config.retrieval.final_top_k,
                         "temperature": config.generation.temperature, "context_tokens": config.context.target_tokens,
                         "max_output_tokens": config.generation.max_output_tokens}

    def get(self):
        values = {row["key"]: json.loads(row["value"]) for row in self.db.all("SELECT * FROM app_settings")}
        return {**self.defaults, **values}

    def patch(self, values):
        with self.db.connection(write=True) as db:
            db.executemany("INSERT INTO app_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           [(key, json.dumps(value)) for key, value in values.items()])
        return self.get()


@dataclass(frozen=True)
class GenerationOptions:
    temperature: float
    max_output_tokens: int
    context_tokens: int


class ChatService:
    def __init__(self, config, chats, retrieval, citations, providers, settings):
        self.config, self.chats, self.retrieval = config, chats, retrieval
        self.citations, self.providers, self.settings = citations, providers, settings

    def prepare(self, request):
        settings = self.settings.get()
        session = str(request.session_id) if request.session_id else None
        warnings = []
        free_chat = request.free_chat
        if request.reuse_message_id:
            if not session:
                raise AppError("reuse_session_required", "Для повторной генерации нужен диалог", 422)
            original = self.chats.message(session, str(request.reuse_message_id))
            if request.query != original["question"]:
                raise AppError("reuse_question_mismatch", "Повторная генерация использует исходный вопрос", 422)
            free_chat = "free_chat" in original["warnings"]
            retrieved = [source for source in original["retrieved"] if self.citations.current(source)]
            if len(retrieved) != len(original["retrieved"]):
                warnings.append("sources_changed")
            history = self.chats.recent(session, original["created_at"])
        else:
            history = self.chats.recent(session)
            query = request.query
            if len(query) < 100 and history:
                query += "\n" + history[-2]["content"][:600]
            retrieved = [] if free_chat else self.retrieval.search(query, request.mode or settings["mode"],
                                              request.top_k or settings["top_k"], request.filters)
        opts = GenerationOptions(**{
            key: getattr(request, key) if getattr(request, key) is not None else settings[key]
            for key in ("temperature", "max_output_tokens", "context_tokens")
        })
        return session, warnings, retrieved, history, opts, free_chat

    def provider(self, request):
        if request.provider == "api":
            if not request.cloud_consent:
                raise AppError("cloud_consent_required", "Передача фрагментов в GigaChat API требует явного разрешения", 403)
            if not self.config.cloud_allowed:
                raise AppError("cloud_disabled", "GigaChat API отключён в настройках сервера (TRAG_CLOUD_ALLOWED).", 403)
            if not self.config.gigachat_credentials or not self.config.gigachat_model:
                raise AppError("cloud_not_configured", "GigaChat API не настроен на сервере", 503)
        return self.providers[request.provider]

    async def stream(self, request, provider, *, recover_timeout=False):
        yield "stage", {"stage": "preparing" if request.free_chat or request.reuse_message_id else "searching"}
        session, warnings, retrieved, history, opts, free_chat = await asyncio.to_thread(self.prepare, request)
        if free_chat:
            warnings.append("free_chat")
        yield "stage", {"stage": "reading", "sources": retrieved}
        started = time.perf_counter()
        sources, messages = [], []
        message_id = uid()
        parts, first_token = [], None
        try:
            if free_chat:
                history, _, _ = await fit_history(provider, request.query, history, None, opts)
                messages = chat_messages(request.query, history, None)
            elif retrieved:
                messages, sources = await build_context(provider, request.query, history, retrieved, opts,
                                                        self.config.context.max_sources)
            if free_chat or sources:
                async for piece in provider.stream(messages, opts):
                    if first_token is None:
                        first_token = time.perf_counter()
                        yield "stage", {"stage": "generating"}
                    parts.append(piece)
                    yield "token", {"text": piece}
        except AppError as exc:
            if not recover_timeout or exc.code != "provider_timeout":
                raise
            warnings.append("generation_timeout")
            await asyncio.to_thread(self.chats.store, session, request.query, "".join(parts), request.provider,
                                    retrieved, [], warnings, message_id)
            yield "error", {"code": exc.code, "message": exc.message, "message_id": message_id if session else None}
            return
        if not sources and not free_chat:
            cyrillic = any("а" <= ch <= "я" or ch == "ё" for ch in request.query.lower())
            answer = ("В доступных фрагментах корпуса недостаточно информации для ответа." if cyrillic
                      else "The available sources do not contain enough information to answer.")
            warnings.append("no_context")
            citations = []
            yield "token", {"text": answer}
        else:
            answer = "".join(parts)
            citations, validation = [], []
            if not free_chat:
                answer, citations, validation = await asyncio.to_thread(self.citations.validate, answer, sources)
            if not answer:
                answer = "Модель вернула пустой ответ."
                validation.append("empty_response")
            warnings.extend(validation)
            event("generation_done", provider=request.provider, sources=len(citations), warnings=validation,
                  ttft_seconds=round(first_token-started, 4) if first_token else None,
                  seconds=round(time.perf_counter()-started, 4))
        await asyncio.to_thread(self.chats.store, session, request.query, answer, request.provider,
                                retrieved, citations, warnings, message_id)
        yield "final", {"answer": answer, "provider": request.provider, "sources": citations,
                        "retrieved_sources": retrieved, "warnings": warnings, "message_id": message_id}
