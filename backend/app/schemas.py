from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.parsing.models import Format

Status = Literal["pending", "processing", "ready", "failed", "requires_ocr", "cancelled"]
Mode = Literal["dense", "bm25", "hybrid"]
Provider = Literal["local", "api"]


class Filters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Format | None = None
    publication_year: int | None = Field(default=None, ge=1000, le=2200)
    language: str | None = Field(default=None, max_length=30)
    document_ids: list[UUID] | None = Field(default=None, max_length=100)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=4000)
    mode: Mode | None = None
    top_k: int | None = Field(default=None, ge=1, le=30)
    filters: Filters = Field(default_factory=Filters)

    @field_validator("query")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Пустой вопрос")
        return value.strip()


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Provider | None = None
    mode: Mode | None = None
    top_k: int | None = Field(default=None, ge=1, le=30)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=32, le=4096)
    context_tokens: int | None = Field(default=None, ge=256, le=16000)


class ChatRequest(SearchRequest):
    provider: Provider
    free_chat: bool = False
    cloud_consent: bool = False
    session_id: UUID | None = None
    reuse_message_id: UUID | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=32, le=4096)
    context_tokens: int | None = Field(default=None, ge=256, le=16000)


class SessionRequest(BaseModel):
    title: str = Field(default="Новый диалог", min_length=1, max_length=120)


class ReindexRequest(BaseModel):
    encoding: str | None = Field(default=None, max_length=32)
