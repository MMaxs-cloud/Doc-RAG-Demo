import asyncio
import base64
import json
import secrets
import shutil
import subprocess
import time
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import psutil
from fastapi import APIRouter, FastAPI, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException

from app.chats import Chats, ChatService, UserSettings, page
from app.citations import CitationResolver
from app.config import config
from app.core.errors import AppError
from app.core.logging import configure, event
from app.documents import Documents
from app.embeddings import EmbeddingHTTP
from app.generation.providers import CloudProvider, LocalProvider
from app.jobs import Jobs
from app.parsing.models import Format
from app.parsing.office import libreoffice
from app.retrieval.qdrant import Qdrant
from app.retrieval.service import Retrieval
from app.schemas import ChatRequest, ReindexRequest, SearchRequest, SessionRequest, SettingsPatch, Status
from app.storage.database import Database
from app.storage.files import Files


def error_response(code, message, status, request_id, headers=None):
    return JSONResponse({"error": {"code": code, "message": message, "request_id": request_id}},
                        status_code=status, headers=headers)


class BodyLimitExceeded(Exception):
    pass


class HttpBoundary:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        self.basic = ("Basic " + base64.b64encode(f"{settings.user}:{settings.password}".encode()).decode()).encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method, path = scope["method"], scope["path"]
        headers = dict(scope["headers"])
        state = scope.setdefault("state", {})
        request_id = state["request_id"] = str(uuid4())
        is_upload = path == "/api/v1/documents/upload"
        limit = min(512, self.settings.documents.max_file_size_mb * 4 + 2) * 1024**2 if is_upload else 256 * 1024
        received, responded = 0, False

        async def guarded_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > limit:
                raise BodyLimitExceeded
            return message

        async def guarded_send(message):
            nonlocal responded
            if message["type"] == "http.response.start":
                responded = True
                message["headers"] = [*message.get("headers", []), (b"x-request-id", request_id.encode()),
                                      (b"x-content-type-options", b"nosniff"),
                                      (b"referrer-policy", b"no-referrer")]
            await send(message)

        async def reject(code, message, status, extra=None):
            await error_response(code, message, status, request_id, extra)(scope, receive, guarded_send)

        if self.settings.environment == "production" and not secrets.compare_digest(headers.get(b"authorization", b""), self.basic):
            await reject("auth_required", "Требуется авторизация", 401, {"WWW-Authenticate": 'Basic realm="Technical RAG"'})
            return
        if method not in ("GET", "HEAD", "OPTIONS"):
            origin = headers.get(b"origin", b"").decode("latin1")
            host = headers.get(b"host", b"").decode("latin1")
            if headers.get(b"sec-fetch-site") == b"cross-site" or (
                origin and (urlsplit(origin).netloc.lower() != host.lower())
            ):
                await reject("origin_denied", "Запрос с другого сайта запрещён", 403)
                return
        length = headers.get(b"content-length", b"0")
        if not length.isdigit() or int(length) > limit:
            await reject("body_too_large", "Размер запроса превышает лимит", 413)
            return
        started = time.perf_counter()
        try:
            await self.app(scope, guarded_receive, guarded_send)
        except BodyLimitExceeded:
            if not responded:
                await reject("body_too_large", "Размер запроса превышает лимит", 413)
        finally:
            route = getattr(scope.get("route"), "path", "unknown")
            event("http_request", request_id=request_id, method=method, route=route,
                  seconds=round(time.perf_counter()-started, 4))


class Services:
    def __init__(self, settings, *, index=None, embeddings=None, providers=None):
        self.config = settings
        self.db, self.files = Database(settings), Files(settings)
        self.jobs = Jobs(self.db)
        self.documents = Documents(self.db, self.files, self.jobs)
        self.index, self.embeddings = index or Qdrant(settings), embeddings or EmbeddingHTTP(settings)
        self.citations = CitationResolver(self.documents)
        self.retrieval = Retrieval(settings, self.documents, self.index, self.embeddings, self.citations)
        self.providers = providers or {"local": LocalProvider(settings), "api": CloudProvider(settings)}
        self.chats, self.settings = Chats(self.db), UserSettings(self.db, settings)
        self.chat = ChatService(settings, self.chats, self.retrieval, self.citations, self.providers, self.settings)

    async def provider_status(self):
        local = await self.providers["local"].healthcheck()
        cloud_configured = bool(self.config.gigachat_model and self.config.gigachat_credentials)
        cloud_available = await self.providers["api"].healthcheck() if cloud_configured else False
        return {
            "local": {"available": local, **self.providers["local"].get_model_info()},
            "api": {"available": cloud_available, "configured": cloud_configured,
                    "cloud_allowed": self.config.cloud_allowed, **self.providers["api"].get_model_info()},
        }


def hardware():
    memory = psutil.virtual_memory()
    result = {"system_ram_mib": memory.total // 1024**2, "system_used_ram_mib": memory.used // 1024**2,
              "backend_rss_mib": psutil.Process().memory_info().rss // 1024**2, "gpu": []}
    binary = shutil.which("nvidia-smi")
    if binary:
        try:
            command = [binary, "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader,nounits"]
            output = subprocess.check_output(command, timeout=3, text=True, stderr=subprocess.DEVNULL)
            for row in output.splitlines():
                name, used, maximum = (field.strip() for field in row.rsplit(",", 2))
                result["gpu"].append({"name": name, "used_mib": int(used), "total_mib": int(maximum)})
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    return result


def diagnose(services):
    def inspect(action):
        try:
            return {"available": True, "detail": action()}
        except AppError as exc:
            return {"available": False, "error": exc.code}

    worker = services.db.one("SELECT updated_at,detail FROM service_state WHERE service='worker'")
    counts = services.db.one("SELECT count(*) AS documents, COALESCE(sum(v.chunk_count),0) AS chunks FROM documents d "
        "LEFT JOIN document_versions v ON v.id=d.active_version WHERE d.deleted_at IS NULL")
    office = services.db.one("SELECT detail FROM service_state WHERE service='libreoffice'")
    if not office and not services.config.parser_socket:
        office = {"detail": "available" if libreoffice(services.config) else "missing"}
    return {"backend": True, "qdrant": inspect(services.index.status), "embeddings": inspect(services.embeddings.get_model_info),
            "worker": {"available": bool(worker and time.time() - worker["updated_at"] < 120), "last_seen": worker},
            "libreoffice": (office or {}).get("detail"), "hardware": hardware(), "counts": counts,
            "jobs": services.db.all("SELECT status,count(*) AS count FROM indexing_jobs GROUP BY status"),
            "recent_errors": services.db.all("SELECT id,document_id,error_code,error_message FROM indexing_jobs "
                                             "WHERE error_code IS NOT NULL ORDER BY finished_at DESC LIMIT 10"),
            "index_version": services.config.index_version}


def create_app(settings=None, *, services=None):
    settings = settings or config()
    services = services or Services(settings)

    @asynccontextmanager
    async def lifespan(api):
        configure()
        await asyncio.to_thread(services.db.initialize)
        event("backend_started", index_version=settings.index_version)
        yield
        for provider in services.providers.values():
            if hasattr(provider, "close"):
                await provider.close()
        for dependency in (services.index, services.embeddings):
            if hasattr(dependency, "http"):
                dependency.http.close()

    api = FastAPI(title="Technical RAG", version=settings.application.version, lifespan=lifespan)
    api.add_middleware(HttpBoundary, settings=settings)
    router = APIRouter(prefix="/api/v1")
    docs, chats, jobs = services.documents, services.chats, services.jobs

    @api.exception_handler(AppError)
    async def handle_error(request, exc):
        return error_response(exc.code, exc.message, exc.status, request.state.request_id)

    @api.exception_handler(RequestValidationError)
    async def validation(request, exc):
        error = exc.errors()[0]
        location = ".".join(map(str, error["loc"]))
        return error_response("invalid_request", f"Проверьте поле {location}", 422, request.state.request_id)

    @api.exception_handler(BodyLimitExceeded)
    async def size_error(request, exc):
        return error_response("body_too_large", "Размер запроса превышает лимит", 413, request.state.request_id)

    @api.exception_handler(HTTPException)
    async def http_error(request, exc):
        message = "Ресурс не найден" if exc.status_code == 404 else "Некорректный HTTP-запрос"
        return error_response("http_error", message, exc.status_code, request.state.request_id, exc.headers)

    @router.get("/health")
    def health():
        return {"status": "ok", "version": settings.application.version}

    @router.get("/system/status")
    async def system():
        info, providers = await asyncio.gather(asyncio.to_thread(diagnose, services), services.provider_status())
        return {**info, "providers": providers}

    @router.post("/documents/upload", status_code=202)
    def upload(files: Annotated[list[UploadFile], File()], encoding: Annotated[str | None, Form(max_length=32)] = None):
        if not 1 <= len(files) <= 25:
            raise AppError("upload_count", "За один запрос можно загрузить от 1 до 25 файлов")
        items = []
        for file in files:
            try:
                items.append({"document": docs.upload(file, encoding)})
            except AppError as exc:
                items.append({"filename": (file.filename or "")[:240], "error": {"code": exc.code, "message": exc.message}})
            finally:
                file.file.close()
        return {"items": items}

    @router.get("/documents")
    def list_documents(query: Annotated[str | None, Query(max_length=200)] = None,
                       format: Format | None = None, status: Status | None = None,
                       offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 25):
        return docs.list(query, format, status, offset, limit)

    @router.get("/documents/{document_id}")
    def document(document_id: UUID):
        return docs.get(str(document_id))

    @router.delete("/documents/{document_id}", status_code=202)
    def delete_document(document_id: UUID):
        return {"job_id": docs.delete(str(document_id))}

    @router.post("/documents/{document_id}/reindex", status_code=202)
    def reindex(document_id: UUID, options: ReindexRequest | None = None):
        return {"job_id": docs.reindex(str(document_id), options.encoding if options else None)}

    @router.get("/documents/{document_id}/file")
    def original(document_id: UUID):
        doc = docs.raw(str(document_id))
        path = docs.files.original(doc["id"])
        if not path.is_file():
            raise AppError("file_unavailable", "Оригинал недоступен", 404)
        return FileResponse(path, media_type="application/octet-stream", filename=doc["filename"],
                            headers={"Cache-Control": "private, no-store", "Content-Security-Policy": "sandbox; default-src 'none'"})

    @router.get("/documents/{document_id}/preview")
    def preview(document_id: UUID):
        doc = docs.raw(str(document_id))
        if doc["format"] == "pdf":
            path, mime = docs.files.original(doc["id"]), "application/pdf"
        elif doc["active_version"] and doc["format"] in ("doc", "docx", "html"):
            suffix = "html" if doc["format"] == "html" else "pdf"
            path = docs.files.preview(doc["id"], doc["active_version"], suffix)
            mime = "text/html" if suffix == "html" else "application/pdf"
        else:
            raise AppError("preview_unavailable", "Откройте текстовое представление", 404)
        if not path.is_file():
            raise AppError("preview_unavailable", "Предпросмотр недоступен", 404)
        return FileResponse(path, media_type=mime, headers={
            "Content-Security-Policy": "sandbox; default-src 'none'; base-uri 'none'; frame-ancestors 'self'",
            "Cache-Control": "private, no-store",
        })

    @router.get("/documents/{document_id}/content")
    def content(document_id: UUID):
        doc = docs.canonical(str(document_id))
        return doc.model_dump(exclude={"original_file_path", "preview_file_path", "source_mapping"})

    @router.get("/jobs")
    def list_jobs(status: Status | None = None, offset: Annotated[int, Query(ge=0)] = 0,
                  limit: Annotated[int, Query(ge=1, le=100)] = 25):
        where, args = (" WHERE status=?", (status,)) if status else ("", ())
        return page(services.db.all("SELECT * FROM indexing_jobs" + where + " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                                    (*args, limit, offset)),
                    services.db.one("SELECT count(*) AS count FROM indexing_jobs" + where, args)["count"], offset, limit)

    @router.get("/jobs/{job_id}")
    def job(job_id: UUID):
        row = services.db.one("SELECT * FROM indexing_jobs WHERE id=?", (str(job_id),))
        if not row:
            raise AppError("job_not_found", "Задание не найдено", 404)
        return row

    @router.post("/jobs/{job_id}/retry", status_code=202)
    def retry(job_id: UUID):
        return {"job_id": jobs.retry(str(job_id))}

    @router.post("/jobs/{job_id}/cancel", status_code=202)
    def cancel(job_id: UUID):
        jobs.cancel(str(job_id))
        return {"job_id": str(job_id)}

    @router.post("/search")
    def search(request: SearchRequest):
        prefs = services.settings.get()
        return {"sources": services.retrieval.search(request.query, request.mode or prefs["mode"],
                                                      request.top_k or prefs["top_k"], request.filters)}

    @router.post("/chat")
    async def chat(options: ChatRequest):
        provider = services.chat.provider(options)
        async for kind, payload in services.chat.stream(options, provider):
            if kind == "final":
                return payload

    @router.post("/chat/stream")
    async def stream(request: Request, options: ChatRequest):
        provider = services.chat.provider(options)

        async def events():
            try:
                async for kind, payload in services.chat.stream(options, provider, recover_timeout=True):
                    if kind == "error":
                        payload["request_id"] = request.state.request_id
                    yield f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            except AppError as exc:
                payload = {"code": exc.code, "message": exc.message, "request_id": request.state.request_id}
                yield "event: error\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @router.get("/chat/sessions")
    def sessions(offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 50):
        return chats.list(offset, limit)

    @router.post("/chat/sessions", status_code=201)
    def new_session(options: SessionRequest):
        return chats.create(options.title)

    @router.get("/chat/sessions/{session_id}")
    def session(session_id: UUID, offset: Annotated[int, Query(ge=0)] = 0,
                limit: Annotated[int, Query(ge=1, le=100)] = 50):
        return chats.get(str(session_id), offset, limit)

    @router.delete("/chat/sessions/{session_id}", status_code=204)
    def delete_session(session_id: UUID):
        chats.delete(str(session_id))

    @router.get("/providers")
    async def providers():
        return await services.provider_status()

    @router.get("/settings")
    def preferences():
        return {**services.settings.get(), "index_version": settings.index_version,
                "embeddings": settings.embeddings.model_dump(), "chunking": settings.chunking.model_dump()}

    @router.patch("/settings")
    def update(values: SettingsPatch):
        return services.settings.patch(values.model_dump(exclude_none=True))

    api.include_router(router)
    return api


app = create_app()
