import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import socket
from contextlib import closing
import subprocess
import sys
import time
from urllib.parse import urlsplit

import httpx
import psutil
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "data" / "run"
REGISTRY = RUN / "processes.json"


def read_registry():
    return json.loads(REGISTRY.read_text()) if REGISTRY.is_file() else []


def tracked(record):
    try:
        process = psutil.Process(record["pid"])
        if abs(process.create_time() - record["created"]) < 0.01 and Path(process.exe()) == Path(record["exe"]):
            return process
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def stop():
    for record in reversed(read_registry()):
        process = tracked(record)
        if not process:
            continue
        for child in process.children(recursive=True):
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        try:
            process.terminate()
            process.wait(15)
        except psutil.NoSuchProcess:
            pass
        except psutil.TimeoutExpired:
            process.kill()
    REGISTRY.unlink(missing_ok=True)


def wait_ready(process, url, name):
    deadline = time.monotonic() + 150
    with httpx.Client(timeout=1, trust_env=False) as client:
        while time.monotonic() < deadline and process.poll() is None:
            try:
                if client.get(url).is_success:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
    raise RuntimeError(f"{name} did not become ready. Inspect data/run/{name}.log.")


def wait_worker(process, database, started):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and process.poll() is None:
        if database.is_file():
            try:
                with closing(sqlite3.connect(database, timeout=1)) as connection:
                    state = connection.execute("SELECT updated_at FROM service_state WHERE service='worker'").fetchone()
                    if state and state[0] >= started:
                        return
            except sqlite3.Error:
                pass
        time.sleep(0.4)
    raise RuntimeError("Worker did not register its heartbeat. Inspect data/run/worker.log.")


def choose_local_model_port(value):
    url = urlsplit(value)
    if url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost") or url.path not in ("", "/"):
        raise RuntimeError("Set a loopback TRAG_LOCAL_URL or use -WithoutLocalModel with an existing server.")
    port = url.port or 8080
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            if port != 8080:
                raise RuntimeError(f"Local model port {port} is already occupied.") from None
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
    return port


def start(options):
    if any(tracked(item) for item in read_registry()):
        raise RuntimeError("Already running. Run scripts/stop.ps1 first.")
    RUN.mkdir(parents=True, exist_ok=True)
    load_dotenv(ROOT / ".env")
    env = dict(os.environ)
    env.setdefault("TRAG_EMBED_PATH", str(ROOT / "data/models/embeddings"))
    env.setdefault("TRAG_DOCLING_ARTIFACTS", str(ROOT / "data/models/docling"))
    env["HF_HUB_OFFLINE"] = "1"
    env["PYTHONUTF8"] = "1"
    qdrant = shutil.which("qdrant") or env.get("TRAG_QDRANT_EXE")
    if not qdrant:
        qdrant = str(ROOT / "data/tools/qdrant/qdrant.exe")
    node = shutil.which("node.exe")
    if not node or not Path(qdrant).is_file():
        raise RuntimeError("Install Node.js and set TRAG_QDRANT_EXE to Qdrant 1.20.2.")
    uvicorn = [sys.executable, "-m", "uvicorn"]
    targets = {
        "qdrant": ([qdrant, "--config-path", "deployment/qdrant.yaml"], ROOT),
        "embeddings": ([*uvicorn, "inference.service:app", "--port", "8091", "--no-access-log"], ROOT),
        "backend": ([*uvicorn, "app.main:app", "--port", "8000", "--no-access-log"], ROOT),
        "worker": ([sys.executable, "-m", "worker.service"], ROOT),
        "frontend": ([node, "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--strictPort"], ROOT / "frontend"),
    }
    if not options.without_local_model:
        llama = shutil.which("llama-server") or env.get("TRAG_LLAMA_EXE")
        if not llama:
            bundled = ROOT / "data/tools/llama/llama-server.exe"
            llama = str(bundled) if bundled.is_file() else None
        model = Path(env.get("TRAG_GGUF_PATH", ROOT / "data/models/GigaChat3.1-10B-A1.8B-q4_K_M.gguf"))
        if not llama or not model.is_file():
            raise RuntimeError("Install llama-server and download the GGUF, or use -WithoutLocalModel.")
        port = choose_local_model_port(env.get("TRAG_LOCAL_URL", "http://127.0.0.1:8080"))
        env["TRAG_LOCAL_URL"] = f"http://127.0.0.1:{port}"
        llama_command = [llama, "--model", str(model), "--alias", "GigaChat3.1-10B-A1.8B",
                         "--host", "127.0.0.1", "--port", str(port), "--ctx-size", "8192", "--parallel", "1",
                         "--jinja", "--n-gpu-layers", str(options.gpu_layers)]
        targets = {"llama": (llama_command, ROOT), **targets}
    readiness = {
        "qdrant": "http://127.0.0.1:6333/",
        "embeddings": "http://127.0.0.1:8091/health",
        "backend": "http://127.0.0.1:8000/api/v1/health",
        "frontend": "http://127.0.0.1:5173/",
        "llama": env.get("TRAG_LOCAL_URL", "http://127.0.0.1:8080").rstrip("/") + "/health",
    }
    records = []
    try:
        for name, (command, cwd) in targets.items():
            started = time.time()
            child_env = env.copy()
            if name != "backend":
                for secret in ("TRAG_GIGACHAT_CREDENTIALS", "TRAG_USER", "TRAG_PASSWORD"):
                    child_env.pop(secret, None)
                child_env.update(PYTHON_DOTENV_DISABLED="1", TRAG_ENVIRONMENT="development", TRAG_CLOUD_ALLOWED="false")
            with (RUN / f"{name}.log").open("ab") as log:
                process = subprocess.Popen(command, cwd=cwd, env=child_env, stdout=log, stderr=log,
                                           stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            info = psutil.Process(process.pid)
            records.append({"name": name, "pid": info.pid, "created": info.create_time(), "exe": info.exe()})
            REGISTRY.write_text(json.dumps(records), encoding="utf-8")
            time.sleep(0.25)
            if process.poll() is not None:
                raise RuntimeError(f"{name} exited with status {process.returncode}.")
            if name in readiness:
                wait_ready(process, readiness[name], name)
            if name == 'worker':
                database = ROOT / env.get('TRAG_DATA', 'data') / 'database/app.db'
                wait_worker(process, database, started)
    except BaseException:
        stop()
        raise
    print("Technical RAG: http://127.0.0.1:5173")
    print("Application logs: data/run")


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("command", choices=["start", "stop"])
    cli.add_argument("--without-local-model", action="store_true")
    cli.add_argument("--gpu-layers", type=int, default=99)
    options = cli.parse_args()
    start(options) if options.command == "start" else stop()
