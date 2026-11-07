"""Typed operator content; actual Qdrant values stay distinct from stored Docs."""

from typing import Literal

from pydantic import BaseModel, Field, JsonValue


class VectorPreview(BaseModel):
    dimension: int
    preview: list[float] = Field(description="向量前 8 个数值；不用于证明语义正确")


class DocumentContent(BaseModel):
    id: str
    search_text: str
    filters: dict[str, JsonValue]
    doc_version: int
    deleted: bool
    vector: VectorPreview


class IndexedContent(BaseModel):
    id: str
    index_key: str | None = Field(
        description="实际 point payload 的 index_key；缺失时为 null"
    )
    search_text: str
    filters: dict[str, JsonValue]


class IndexedVectorContent(IndexedContent):
    vector: VectorPreview


class QdrantContent(BaseModel):
    status: Literal["ok", "unavailable", "unsupported"]
    error: str = ""
    vector_documents: list[IndexedVectorContent] = Field(default_factory=list)
    keyword_documents: list[IndexedContent] = Field(default_factory=list)
