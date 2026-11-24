import os
import time

import psutil
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.config import Config, config
from app.core.logging import configure, event
from app.embeddings import tokenizer

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_MODULES_CACHE", str((Path.cwd() / "data/models/hf-modules").resolve()))


class GigaEmbeddings:
    def __init__(self, settings: Config):
        import torch
        from transformers import AutoModel

        self.torch = torch
        self.settings = settings
        self.lock = Lock()
        cfg = settings.embeddings
        local = str(settings.embed_path) if settings.embed_path else None
        self.tokenizer = tokenizer(cfg.model, cfg.revision, local)
        self.device = "cuda" if settings.embed_device != "cpu" and torch.cuda.is_available() else "cpu"
        if self.device == "cpu":
            torch.set_num_threads(min(8, os.cpu_count() or 1))
        self.model = AutoModel.from_pretrained(
            local or cfg.model, revision=cfg.revision, local_files_only=True, trust_remote_code=True,
            dtype=torch.bfloat16 if self.device == "cuda" else torch.float32, attn_implementation="sdpa",
        ).eval()
        try:
            self.model.to(self.device)
        except torch.cuda.OutOfMemoryError:
            self.cpu_fallback()
        if self.model.config.hidden_size != cfg.dimension:
            raise RuntimeError("Embedding dimension differs from index configuration")
        self.max_tokens = min(self.model.config.max_position_embeddings, self.tokenizer.model_max_length)

    def cpu_fallback(self):
        self.device = "cpu"
        self.model.to("cpu", dtype=self.torch.float32)
        self.torch.set_num_threads(min(8, os.cpu_count() or 1))
        self.torch.cuda.empty_cache()
        event("embeddings_cpu_fallback")

    def get_model_info(self):
        cfg = self.settings.embeddings
        return {"model": cfg.model, "revision": cfg.revision, "tokenizer_revision": cfg.revision,
                "dimension": cfg.dimension, "pooling": "attention-mask mean", "normalization": "l2",
                "query_instruction": cfg.query_instruction, "device": self.device,
                "precision": str(self.model.dtype), "max_input_tokens": self.max_tokens,
                "rss_mib": round(psutil.Process().memory_info().rss / 1024**2, 1),
                "cuda_peak_mib": round(self.torch.cuda.max_memory_allocated() / 1024**2, 1)
                if self.device == "cuda" else None,
                "index_version": self.settings.index_version}

    def _batch(self, texts):
        torch = self.torch
        encoded = self.tokenizer(texts, padding=True, truncation=False, return_tensors="pt")
        if encoded["input_ids"].shape[1] > self.max_tokens or not bool(encoded["attention_mask"].any(dim=1).all()):
            raise ValueError("Invalid embedding input length")
        encoded = encoded.to(self.device)
        with torch.inference_mode():
            hidden = self.model(**encoded).last_hidden_state
            mask = encoded["attention_mask"].unsqueeze(-1)
            pooled = (hidden.float() * mask).sum(1) / mask.sum(1).clamp_min(1)
            vectors = torch.nn.functional.normalize(pooled, p=2, dim=-1)
            if vectors.shape[1] != self.settings.embeddings.dimension or not bool(torch.isfinite(vectors).all()):
                raise RuntimeError("Model returned invalid embeddings")
            if not bool((vectors.norm(dim=1) > 0.99).all()):
                raise RuntimeError("Model returned zero-length embeddings")
            return vectors.cpu().tolist()

    def _encoded(self, texts):
        try:
            return self._batch(texts)
        except self.torch.cuda.OutOfMemoryError:
            pass
        self.torch.cuda.empty_cache()
        if len(texts) > 1:
            middle = len(texts) // 2
            return self._encoded(texts[:middle]) + self._encoded(texts[middle:])
        if self.device == "cuda":
            self.cpu_fallback()
            return self._batch(texts)
        raise RuntimeError("Not enough memory for embedding inference")

    def embed_documents(self, texts):
        started = time.perf_counter()
        count = self.settings.embeddings.batch_size
        with self.lock:
            vectors = [vector for i in range(0, len(texts), count) for vector in self._encoded(texts[i:i+count])]
        event("embedding_batch", inputs=len(texts), seconds=round(time.perf_counter() - started, 4))
        return vectors

    def embed_query(self, query):
        return self.embed_documents([f"Instruct: {self.settings.embeddings.query_instruction}\nQuery: {query}"])[0]


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=128)
    query: bool = False


@asynccontextmanager
async def lifespan(app):
    configure()
    app.state.model = GigaEmbeddings(config())
    yield


app = FastAPI(title="Technical RAG Embedding Inference", lifespan=lifespan)


@app.get("/health")
def health():
    return app.state.model.get_model_info()


@app.post("/embed")
def embed(request: EmbedRequest):
    if any(not text.strip() or len(text) > 60_000 for text in request.texts):
        raise HTTPException(422, "Invalid embedding input")
    if request.query and len(request.texts) != 1:
        raise HTTPException(422, "Exactly one query is required")
    model = app.state.model
    try:
        vectors = [model.embed_query(request.texts[0])] if request.query else model.embed_documents(request.texts)
    except ValueError:
        raise HTTPException(422, "Invalid embedding input length") from None
    return {"vectors": vectors, "index_version": model.settings.index_version}
