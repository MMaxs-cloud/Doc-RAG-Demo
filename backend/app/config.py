import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator


class Application(BaseModel):
    name: str
    version: Literal["0.6"]


class Documents(BaseModel):
    max_file_size_mb: int = Field(gt=0, le=512)
    max_docx_uncompressed_mb: int = Field(gt=0, le=1024)


class Parsing(BaseModel):
    timeout_seconds: int = Field(ge=10, le=1800)
    conversion_timeout_seconds: int = Field(ge=5, le=600)
    ocr_enabled: bool
    pdf_device: Literal["cpu", "cuda", "auto"]
    pdf_fallback: bool


class Embeddings(BaseModel):
    model: Literal["ai-sage/Giga-Embeddings-instruct-480M-0826"]
    revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    dimension: int = Field(gt=0)
    batch_size: int = Field(gt=0, le=128)
    query_instruction: str = Field(min_length=1)


class Chunking(BaseModel):
    size: Literal[512]
    overlap: Literal[80]


class Retrieval(BaseModel):
    dense_top_k: int = Field(gt=0, le=100)
    bm25_top_k: int = Field(gt=0, le=100)
    final_top_k: int = Field(gt=0, le=50)
    rrf_k: int = Field(gt=0)
    max_chunks_per_document: int = Field(gt=0)
    diversity_enabled: bool


class Context(BaseModel):
    target_tokens: int = Field(gt=0)
    max_sources: int = Field(gt=0, le=50)


class Generation(BaseModel):
    local_window: int = Field(ge=2048)
    api_window: int = Field(ge=2048)
    temperature: float = Field(ge=0, le=2)
    max_output_tokens: int = Field(gt=0)
    timeout_seconds: int = Field(gt=0)


class Jobs(BaseModel):
    lease_seconds: int = Field(ge=15)
    attempts: int = Field(ge=1, le=10)


class Config(BaseModel):
    application: Application
    documents: Documents
    parsing: Parsing
    embeddings: Embeddings
    chunking: Chunking
    retrieval: Retrieval
    context: Context
    generation: Generation
    jobs: Jobs
    data: Path
    environment: Literal["development", "production"] = "development"
    user: str = ""
    password: str = Field(default="", repr=False)
    qdrant_url: str
    embed_url: str
    embed_path: Path | None = None
    embed_device: Literal["auto", "cpu", "cuda"] = "auto"
    docling_artifacts: Path | None = None
    parser_socket: Path | None = None
    soffice: Path | None = None
    allow_native_office: bool = False
    local_url: str
    local_model: str
    cloud_allowed: bool = False
    gigachat_credentials: str = Field(default="", repr=False)
    gigachat_model: str = ""
    gigachat_scope: Literal["GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"]
    gigachat_ca_file: Path | None = None

    @model_validator(mode="after")
    def validate_security(self):
        if self.environment == "production" and (not self.user or not self.password):
            raise ValueError("Production requires TRAG_USER and TRAG_PASSWORD")
        if self.environment == "production" and self.cloud_allowed and self.gigachat_scope == "GIGACHAT_API_PERS":
            raise ValueError("Production cloud use requires a commercial GigaChat scope")
        return self

    @property
    def index_version(self) -> str:
        signature = {
            "embeddings": self.embeddings.model_dump(exclude={"batch_size"}),
            "chunking": self.chunking.model_dump(),
            "pooling": "attention-mask-mean-l2",
            "bm25": "qdrant-text-word",
            "distance": "cosine",
            "vector_storage": "float32",
            "inference_precision": "bf16-cuda-fp32-cpu-fallback",
        }
        return hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:20]

    @property
    def parser_signature(self) -> str:
        return hashlib.sha256(json.dumps(self.parsing.model_dump(), sort_keys=True).encode()).hexdigest()[:12]


@lru_cache
def config() -> Config:
    load_dotenv()
    env = os.environ
    path = Path(env.get("TRAG_CONFIG", "configs/default.yaml"))
    defaults = yaml.safe_load(path.read_text(encoding="utf-8"))
    values = {
        "data": Path(env.get("TRAG_DATA", "data")).resolve(),
        "qdrant_url": env.get("TRAG_QDRANT_URL", "http://127.0.0.1:6333"),
        "embed_url": env.get("TRAG_EMBED_URL", "http://127.0.0.1:8091"),
        "local_url": env.get("TRAG_LOCAL_URL", "http://127.0.0.1:8080"),
        "local_model": env.get("TRAG_LOCAL_MODEL", "GigaChat3.1-10B-A1.8B"),
        "gigachat_scope": env.get("TRAG_GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
    }
    for key in ("environment", "user", "password", "embed_path", "embed_device",
                "docling_artifacts", "parser_socket", "soffice", "allow_native_office",
                "cloud_allowed", "gigachat_credentials", "gigachat_model", "gigachat_ca_file"):
        value = env.get(f"TRAG_{key.upper()}")
        if value:
            values[key] = value
    return Config.model_validate({**defaults, **values})
