"""
统一错误码处理模块

职责：
1. 定义 infrastructure 层异常映射表
2. 实现统一的 error_code 解析函数（含 cause 链检查）
3. 实现层级 fallback 机制
"""
from __future__ import annotations

from typing import Optional


# =============================================================================
# Infrastructure 层异常映射表
# =============================================================================

# 注意：仅映射明确的 infrastructure 自定义异常，不映射通用异常（如 ValueError）
INFRA_EXCEPTION_MAPPING: dict[type[Exception], str] = {
    # Embedding
    # EmbeddingAPIError: "BCSFUSE-INFRA-EMBEDDING-API-ERROR",  # 首期不覆盖

    # LLM
    # AnthropicProviderError: "BCSFUSE-INFRA-LLM-API-ERROR",  # 首期不覆盖

    # Storage
    IOError: "BCSFUSE-INFRA-STORAGE-ERROR",
    FileNotFoundError: "BCSFUSE-INFRA-STORAGE-NOT-FOUND",
}


# =============================================================================
# 层级 fallback 定义
# =============================================================================

LAYER_FALLBACK: dict[str, str] = {
    "interfaces": "BCSFUSE-IF-INTERNAL-ERROR",
    "application": "BCSFUSE-APP-INTERNAL-ERROR",
    "domain": "BCSFUSE-DOM-INTERNAL-ERROR",
    "infrastructure": "BCSFUSE-INFRA-INTERNAL-ERROR",
}

DEFAULT_FALLBACK = "BCSFUSE-INTERNAL-ERROR"


# =============================================================================
# 核心解析函数
# =============================================================================

def resolve_error_code(exc: Exception, layer: Optional[str] = None) -> str:
    """
    从异常实例解析标准错误码

    解析优先级：
    1. 检查异常自身的 error_code 属性（DomainException 已在构造时生成）
    2. 检查异常的 __cause__ 链（递归查找）
    3. 查找 INFRA_EXCEPTION_MAPPING 映射表
    4. 使用层级 fallback（需调用方提供 layer 参数）

    Args:
        exc: 异常实例
        layer: 调用方所在层级，用于 fallback（"interfaces" | "application" | "domain" | "infrastructure"）

    Returns:
        标准错误码字符串
    """
    # 1. 检查异常自身的 error_code 属性
    if hasattr(exc, 'error_code') and exc.error_code:
        return exc.error_code

    # 2. 检查 __cause__ 链（异常链中是否有携带 error_code 的异常）
    current = exc.__cause__ or exc.__context__
    while current is not None:
        if hasattr(current, 'error_code') and current.error_code:
            return current.error_code
        current = current.__cause__ or current.__context__

    # 3. 查找 INFRA_EXCEPTION_MAPPING 映射表
    # 注意：需要按异常类型的继承层级从具体到通用排序查找
    # FileNotFoundError 是 IOError(OSError) 的子类，需要先匹配
    sorted_mapping = sorted(
        INFRA_EXCEPTION_MAPPING.items(),
        key=lambda item: len(item[0].__mro__),
        reverse=True  # 更具体的类型（更长 MRO）先匹配
    )
    for exc_type, code in sorted_mapping:
        if isinstance(exc, exc_type):
            return code

    # 4. 使用层级 fallback
    if layer and layer in LAYER_FALLBACK:
        return LAYER_FALLBACK[layer]

    return DEFAULT_FALLBACK


def resolve_error_code_from_layer(layer: str) -> str:
    """
    根据层级获取 fallback 错误码

    Args:
        layer: 层级名称（"interfaces" | "application" | "domain" | "infrastructure"）

    Returns:
        该层级的 fallback 错误码
    """
    return LAYER_FALLBACK.get(layer, DEFAULT_FALLBACK)


__all__ = [
    "INFRA_EXCEPTION_MAPPING",
    "LAYER_FALLBACK",
    "DEFAULT_FALLBACK",
    "resolve_error_code",
    "resolve_error_code_from_layer",
]