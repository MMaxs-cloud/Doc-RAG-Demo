"""Start isolated loopback services for real model and Qdrant verification."""
import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


def wait_for(url, process, log):
    deadline = time.monotonic() + 150
    with httpx.Client(trust_env=False, timeout=1) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                if client.get(url).is_success:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.35)
    raise RuntimeError(f"Service {url} failed to start. See {log}.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--qdrant", type=Path, required=True)
    parser.add_argument("--embedding", type=Path, required=True)
    parser.add_argument("--soffice", type=Path)
    parser.add_argument("--llama", type=Path)
    parser.add_argument("--gguf", type=Path)
    parser.add_argument("--docling", type=Path)
    parser.add_argument("--gpu-layers", type=int, default=0)
    parser.add_argument("--embed-device", choices=["auto", "cpu", "cuda"], default="auto")
    args = parser.parse_args()
    os.chdir(ROOT)
    root = ROOT / "data"
    root.mkdir(exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="live-", dir=root))
    print(f"Test runtime: {run}", flush=True)
    logs = run / "logs"
    logs.mkdir()
    env = dict(os.environ, TRAG_QDRANT_URL="http://127.0.0.1:16333", TRAG_EMBED_URL="http://127.0.0.1:18091",
               TRAG_LOCAL_URL="http://127.0.0.1:18080", TRAG_DATA=str(run / "application"), TRAG_LIVE_TEST="1",
               TRAG_EMBED_PATH=str(args.embedding.resolve()), TRAG_EMBED_DEVICE=args.embed_device,
               QDRANT__SERVICE__HOST="127.0.0.1",
               QDRANT__SERVICE__HTTP_PORT="16333", QDRANT__SERVICE__GRPC_PORT="16334",
               QDRANT__STORAGE__STORAGE_PATH=str(run / "qdrant"), QDRANT__TELEMETRY_DISABLED="true",
               PYTHONPATH=str(ROOT / "backend"), PYTHONUTF8="1", PYTHON_DOTENV_DISABLED="1", TRAG_ENVIRONMENT="development")
    if args.soffice:
        env.update(TRAG_SOFFICE=str(args.soffice.resolve()), TRAG_ALLOW_NATIVE_OFFICE="true")
    if args.docling:
        env["TRAG_DOCLING_ARTIFACTS"] = str(args.docling.resolve())
    targets = [
        ("qdrant", [str(args.qdrant.resolve())], env["TRAG_QDRANT_URL"] + "/"),
        ("embeddings", [sys.executable, "-m", "uvicorn", "inference.service:app", "--port", "18091", "--no-access-log"],
         env["TRAG_EMBED_URL"] + "/health"),
    ]
    if args.llama and args.gguf:
        env["TRAG_LIVE_LLM"] = "1"
        targets.append(("llama", [str(args.llama.resolve()), "-m", str(args.gguf.resolve()), "--jinja",
                       "--alias", "GigaChat3.1-10B-A1.8B", "--ctx-size", "8192", "--parallel", "1",
                       "--port", "18080", "--n-gpu-layers", str(args.gpu_layers)], env["TRAG_LOCAL_URL"] + "/health"))

    def shutdown(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    with ExitStack() as resources:
        for name, command, url in targets:
            log_path = logs / f"{name}.log"
            log = resources.enter_context(log_path.open("wb"))
            process = subprocess.Popen(command, env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            resources.callback(shutdown, process)
            wait_for(url, process, log_path)
        completed = subprocess.run([sys.executable, "-m", "pytest", "-s", "-q", "backend/tests/test_live.py"],
                                   env=env, check=False)
        return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
