import json
import os
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID

import psutil

from app.config import config
from app.core.errors import AppError
from app.core.logging import event
from app.parsing.models import CanonicalDocument
from app.storage.files import atomic_write

ENV_KEYS = (
    "PATH", "SystemRoot", "WINDIR", "SYSTEMDRIVE", "ProgramFiles", "ProgramFiles(x86)",
    "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PYTHONPATH", "TRAG_DATA", "TRAG_CONFIG",
    "TRAG_DOCLING_ARTIFACTS", "TRAG_PDF_DEVICE", "TRAG_SOFFICE", "TRAG_ALLOW_NATIVE_OFFICE", "TRAG_ISOLATED_PARSER",
)


def child_environment(working: Path):
    return {**{key: os.environ[key] for key in ENV_KEYS if key in os.environ},
            "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHON_DOTENV_DISABLED": "1", "TEMP": str(working), "TMP": str(working),
            "TMPDIR": str(working), "PYTHONUTF8": "1", "OMP_NUM_THREADS": "4"}


def kill_tree(process, children):
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    for child in children:
        try:
            child.kill()
        except psutil.Error:
            pass
    process.wait(timeout=10)
    if children:
        psutil.wait_procs(children, timeout=2)


def run_child(working: Path, timeout: int):
    with subprocess.Popen(
        [sys.executable, "-m", "app.parsing.runner", "parse", str(working)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=child_environment(working), start_new_session=os.name != "nt",
    ) as process:
        deadline = time.monotonic() + timeout
        parent = psutil.Process(process.pid)
        descendants = set()
        try:
            while process.poll() is None:
                try:
                    children = parent.children(recursive=True)
                    descendants.update(children)
                    memory = sum(proc.memory_info().rss for proc in [parent, *children] if proc.is_running())
                except psutil.Error:
                    memory = 0
                if time.monotonic() > deadline or memory > 5 * 1024**3:
                    raise AppError("parser_limit", "Превышен лимит времени или памяти парсера")
                time.sleep(0.1)
        finally:
            if process.poll() is None or descendants:
                kill_tree(process, list(descendants))
        if not (working / "outcome.json").is_file():
            raise AppError("parser_failure", "Процесс парсинга завершился без результата")


def parse_child(working: Path):
    if Path.cwd() == working:
        raise RuntimeError("Invalid parser working directory")
    if os.name != "nt":
        import resource
        resource.setrlimit(resource.RLIMIT_FSIZE, (384 * 1024**2, 384 * 1024**2))
        limits = config().parsing.timeout_seconds * 4
        resource.setrlimit(resource.RLIMIT_CPU, (limits, limits))
    from app.parsing.registry import DocumentParserRegistry
    try:
        record = json.loads((working / "record.json").read_bytes())
        canonical = DocumentParserRegistry().parse(working, record, config())
        atomic_write(working / "canonical.json", canonical.model_dump_json().encode())
        response = {"ok": True}
    except AppError as exc:
        response = {"code": exc.code, "message": exc.message}
    except Exception:
        response = {"code": "parse_failed", "message": "Парсер не смог прочитать структуру документа"}
    atomic_write(working / "outcome.json", json.dumps(response, ensure_ascii=False).encode())


def serve():
    settings = config()
    if not settings.parser_socket or os.name == "nt" or os.environ.get("TRAG_ISOLATED_PARSER") != "1":
        raise RuntimeError("Set TRAG_PARSER_SOCKET and run parser with networking disabled")
    socket_path = settings.parser_socket
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            try:
                message = self.rfile.readline(80).decode().strip()
                if message == "status":
                    from app.parsing.office import libreoffice
                    self.wfile.write(b"available\n" if libreoffice(settings) else b"missing\n")
                    return
                token = str(UUID(message))
                stage = settings.data / "temporary" / token
                if not stage.is_dir() or stage.is_symlink():
                    raise ValueError
                run_child(stage, settings.parsing.timeout_seconds)
                self.wfile.write(b"ok\n")
            except AppError as exc:
                self.wfile.write(json.dumps({"code": exc.code, "message": exc.message}).encode() + b"\n")
            except (ValueError, OSError):
                self.wfile.write(b"invalid\n")

    with socketserver.UnixStreamServer(str(socket_path), Handler) as service:
        socket_path.chmod(0o770)
        service.serve_forever()


def office_status(settings):
    if not settings.parser_socket:
        from app.parsing.office import libreoffice
        return "available" if libreoffice(settings) else "missing"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(str(settings.parser_socket))
            connection.sendall(b"status\n")
            with connection.makefile("rb") as incoming:
                status = incoming.readline(30).decode().strip()
        return status if status in ("available", "missing") else "parser_unavailable"
    except OSError:
        return "parser_unavailable"


def clear_work_directory(directory: Path):
    target = directory
    if os.name == "nt":
        absolute = str(directory.resolve())
        target = Path("\\\\?\\UNC\\" + absolute[2:] if absolute.startswith("\\\\") else "\\\\?\\" + absolute)
    for attempt in range(4):
        try:
            shutil.rmtree(target)
            return
        except OSError as exc:
            if isinstance(exc, FileNotFoundError) and not directory.exists():
                return
            if attempt == 3:
                event("parser_cleanup_deferred", error_type=type(exc).__name__)
                return
        time.sleep(0.2 * (attempt + 1))


def parse_document(settings, files, doc: dict, version: str):
    directory = files.temporary / str(UUID(version))
    directory.mkdir()
    try:
        shutil.copyfile(files.original(doc["id"]), directory / f"input.{doc['format']}")
        record = {key: doc[key] for key in
                  ("id", "filename", "format", "mime", "sha256", "size", "created_at", "encoding_hint")}
        atomic_write(directory / "record.json", json.dumps(record, ensure_ascii=False).encode())
        if settings.parser_socket:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    connection.settimeout(settings.parsing.timeout_seconds + 10)
                    connection.connect(str(settings.parser_socket))
                    connection.sendall(version.encode() + b"\n")
                    with connection.makefile("rb") as incoming:
                        reply = incoming.readline(4096)
                if reply.startswith(b"{"):
                    error = json.loads(reply)
                    raise AppError(error["code"], error["message"])
                if reply != b"ok\n":
                    raise OSError
            except OSError:
                raise AppError("parser_unavailable", "Изолированный сервис парсинга недоступен", 503,
                               retryable=True) from None
        else:
            run_child(directory, settings.parsing.timeout_seconds)
        response = json.loads((directory / "outcome.json").read_bytes())
        if not response.get("ok"):
            raise AppError(response["code"], response["message"])
        canonical = CanonicalDocument.model_validate_json((directory / "canonical.json").read_bytes())
        if canonical.preview_file_path:
            suffix = Path(canonical.preview_file_path).suffix[1:]
            preview = files.preview(doc["id"], version, suffix)
            atomic_write(preview, (directory / f"preview.{suffix}").read_bytes())
            canonical.preview_file_path = preview.relative_to(settings.data).as_posix()
        return canonical
    finally:
        clear_work_directory(directory)


if __name__ == "__main__":
    if sys.argv[1:] == ["serve"]:
        serve()
    else:
        parse_child(Path(sys.argv[2]))
