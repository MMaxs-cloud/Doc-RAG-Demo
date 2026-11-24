import argparse
import os
from pathlib import Path
import subprocess
import sys

from app.config import config

GGUF = "GigaChat3.1-10B-A1.8B-q4_K_M.gguf"
GGUF_REVISION = "97045b260251cfa86f5ad25638fa2dd074153446"


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--lightning", action="store_true")
    args.add_argument("--docling", action="store_true")
    values = args.parse_args()
    from huggingface_hub import hf_hub_download, snapshot_download

    settings = config()
    root = Path("data/models").resolve()
    root.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=settings.embeddings.model, revision=settings.embeddings.revision,
                      local_dir=root / "embeddings")
    if values.lightning:
        hf_hub_download(repo_id="ai-sage/GigaChat3.1-10B-A1.8B-GGUF", filename=GGUF,
                        revision=GGUF_REVISION, local_dir=root)
    if values.docling:
        tool = Path(sys.executable).with_name("docling-tools.exe" if os.name == "nt" else "docling-tools")
        subprocess.run([str(tool), "models", "download", "layout", "tableformer", "tableformerv2",
                        "--output-dir", str(root / "docling")], check=True)


if __name__ == "__main__":
    main()
