"""
OSS Schemas Module

Minimal API schemas for OSS business routes.
These schemas are designed to avoid importing from src.interfaces.api.*

IMPORTANT: DO NOT import from src.interfaces.api.* as it triggers:
    __init__.py -> app.py -> recommend_routes.py -> drm_resource.py -> Layotto init
"""
from typing import Optional, List
from pydantic import BaseModel, Field


# ========================================
# Worker Schemas
# ========================================

class WorkerCreateRequest(BaseModel):
    """Worker creation request (minimal fields for OSS test mode)."""
    worker_id: str = Field(..., min_length=1, description="Worker ID")
    name: str = Field(..., min_length=1, description="Worker name")
    description: Optional[str] = Field(None, description="Worker description")
    skills: List[str] = Field(default_factory=list, description="Worker skills")
    is_public: bool = Field(default=True, description="Public visibility")


class WorkerUpdateRequest(BaseModel):
    """Worker update request."""
    name: Optional[str] = Field(None, description="Worker name")
    description: Optional[str] = Field(None, description="Worker description")
    skills: Optional[List[str]] = Field(None, description="Worker skills")
    is_public: Optional[bool] = Field(None, description="Public visibility")


class WorkerResponse(BaseModel):
    """Worker response."""
    id: str
    name: str
    description: Optional[str] = None
    skills: List[str] = []
    is_public: bool = True
    status: str = "inactive"


class WorkerListResponse(BaseModel):
    """Worker list response."""
    success: bool
    items: List[WorkerResponse]
    total: int


# ========================================
# Profile Schemas
# ========================================

class ProfileUpsertRequest(BaseModel):
    """Profile upsert request."""
    profile_id: str = Field(..., min_length=1, description="Profile ID")
    content: str = Field(..., min_length=1, description="Profile content")
    metadata: Optional[dict] = Field(default_factory=dict, description="Profile metadata")


class ProfileResponse(BaseModel):
    """Profile response."""
    profile_id: str
    worker_id: str
    content: str
    is_active: bool = False
    metadata: dict = {}


class ProfileListResponse(BaseModel):
    """Profile list response."""
    success: bool
    items: List[ProfileResponse]
    total: int


class ProfileActivateResponse(BaseModel):
    """Profile activation response."""
    success: bool
    worker_id: str
    profile_id: str
    indexed: bool
    vector_count: int


# ========================================
# Search Schemas
# ========================================

class SearchRequest(BaseModel):
    """Search request."""
    query: str = Field(..., min_length=1, description="Search query")
    top_k: int = Field(default=10, ge=1, le=50, description="Number of results")


class SearchResult(BaseModel):
    """Single search result."""
    worker_id: str
    profile_id: str
    score: float
    content: Optional[str] = None


class SearchResponse(BaseModel):
    """Search response."""
    success: bool
    query: str
    top_k: int
    results_count: int
    results: List[SearchResult]


class SearchStatsResponse(BaseModel):
    """Search stats response."""
    vector_backend: str
    vector_count: int
    indexed_workers: int
    provider_mode: str


# ========================================
# Generic Response Schemas
# ========================================

class SuccessResponse(BaseModel):
    """Generic success response."""
    success: bool = True
    message: Optional[str] = None


class ErrorResponse(BaseModel):
    """Generic error response."""
    success: bool = False
    code: str
    message: str


__all__ = [
    "WorkerCreateRequest",
    "WorkerUpdateRequest",
    "WorkerResponse",
    "WorkerListResponse",
    "ProfileUpsertRequest",
    "ProfileResponse",
    "ProfileListResponse",
    "ProfileActivateResponse",
    "SearchRequest",
    "SearchResult",
    "SearchResponse",
    "SearchStatsResponse",
    "SuccessResponse",
    "ErrorResponse",
]