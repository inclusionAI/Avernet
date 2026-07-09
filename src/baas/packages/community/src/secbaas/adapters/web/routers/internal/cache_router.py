"""ZCache 读写 RESTful API 路由

提供基于 key 的缓存写入（带 TTL）与读取能力。
"""

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from secbaas.api import ApiResponse, DomainError
from secbaas.bootstrap import ApplicationContainer
from secbaas.spi.cache import CachePlugin

router = APIRouter(prefix="/api/v1/cache", tags=["缓存"])


class CacheSetRequest(BaseModel):
    """缓存写入请求体"""

    value: str = Field(..., description="待缓存的字符串值")
    ttl_seconds: int = Field(
        ..., gt=0, le=30 * 24 * 3600, description="过期时间（秒），1 到 30 天"
    )


class CacheSetResponse(BaseModel):
    """缓存写入响应"""

    key: str
    ttl_seconds: int


class CacheGetResponse(BaseModel):
    """缓存读取响应"""

    key: str
    value: str


class CacheKeyNotFoundError(DomainError):
    error_code = "CACHE_KEY_NOT_FOUND"
    http_status = 404


@router.post(
    "/{key}",
    summary="按 key 写入缓存（带 TTL）",
    response_model=ApiResponse[CacheSetResponse],
)
@inject
async def set_cache(
    key: str,
    body: CacheSetRequest,
    cache: CachePlugin = Depends(Provide[ApplicationContainer.plugins.cache_plugin]),
) -> ApiResponse[CacheSetResponse]:
    """将 ``value`` 写入缓存，超过 ``ttl_seconds`` 后自动过期。"""
    cache.set(key, body.value, ttl_seconds=body.ttl_seconds)
    return ApiResponse(data=CacheSetResponse(key=key, ttl_seconds=body.ttl_seconds))


@router.get(
    "/{key}",
    summary="按 key 读取缓存",
    response_model=ApiResponse[CacheGetResponse],
)
@inject
async def get_cache(
    key: str,
    cache: CachePlugin = Depends(Provide[ApplicationContainer.plugins.cache_plugin]),
) -> ApiResponse[CacheGetResponse]:
    """读取缓存中 ``key`` 对应的值，未命中返回 404。"""
    value = cache.get(key)
    if value is None:
        raise CacheKeyNotFoundError(f"cache key not found: {key}")
    return ApiResponse(data=CacheGetResponse(key=key, value=value))
