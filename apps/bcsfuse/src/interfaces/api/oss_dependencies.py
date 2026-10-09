"""
OSS API Dependency Layer

Provides dependency injection for OSS routes using the provider registry.
This layer is designed to:
1. Access ApplicationContext from request.app.state.context
2. Use provider registry for dependency resolution
3. Avoid importing internal code (DRM, Layotto, Sofa, ZDAS)
4. Avoid external service connections during import

This module is part of the S6 OSS migration.
"""

from typing import Optional, Any
from fastapi import Request

from src.bootstrap.application_context import ApplicationContext


def get_app_context(request: Request) -> ApplicationContext:
    """
    Get ApplicationContext from request.app.state.

    Args:
        request: FastAPI request object

    Returns:
        ApplicationContext instance

    Raises:
        RuntimeError: If context is not initialized
    """
    context = getattr(request.app.state, 'context', None)
    if context is None:
        raise RuntimeError("ApplicationContext not initialized in app.state")
    return context


def get_provider_registry(request: Request):
    """
    Get provider registry from ApplicationContext.

    Args:
        request: FastAPI request object

    Returns:
        Provider registry dict
    """
    context = get_app_context(request)
    return context.registry


def get_provider(request: Request, name: str) -> Optional[Any]:
    """
    Get a specific provider from the registry.

    Args:
        request: FastAPI request object
        name: Provider name (e.g., 'worker_registry_store', 'worker_profile_content_store')

    Returns:
        Provider instance if available, None otherwise
    """
    registry = get_provider_registry(request)
    return registry.get(name)


# =============================================================================
# OSS-Safe Dependency Providers
# =============================================================================

def get_worker_store(request: Request):
    """
    Get worker store from provider registry.

    OSS-safe alternative to worker_dependencies.get_registry_store().
    """
    return get_provider(request, 'worker_registry_store')


def get_profile_store(request: Request):
    """
    Get profile store from provider registry.

    OSS-safe alternative to worker_dependencies.get_worker_profile_content_service().
    """
    return get_provider(request, 'worker_profile_content_store')


def get_embedding_provider(request: Request):
    """
    Get embedding provider from provider registry.

    OSS-safe alternative to fusion_dependencies._get_embedding_generator().
    """
    return get_provider(request, 'embedding_provider')


def get_vector_store(request: Request):
    """
    Get vector store from provider registry.

    OSS-safe alternative to fusion_dependencies._get_profile_embedding_store().
    """
    return get_provider(request, 'vector_store')


def get_llm_gateway(request: Request):
    """
    Get LLM gateway from provider registry.

    OSS-safe alternative to fusion_dependencies._get_llm_gateway_service().
    """
    return get_provider(request, 'llm_gateway')


def get_fusion_service(request: Request):
    """
    Get fusion service from provider registry.

    OSS-safe alternative to fusion_dependencies.get_group_fusion_service().
    """
    return get_provider(request, 'fusion_service')


def get_recommend_service(request: Request):
    """
    Get recommend service from provider registry.

    OSS-safe alternative to recommend route dependencies.
    """
    return get_provider(request, 'recommend_service')


def get_search_service(request: Request):
    """
    Get search service from provider registry.

    OSS-safe alternative for search dependencies.
    """
    return get_provider(request, 'search_service')


# =============================================================================
# Provider Availability Checks
# =============================================================================

def check_provider_availability(request: Request) -> dict:
    """
    Check which providers are available in the registry.

    Returns:
        Dict mapping provider name to availability status
    """
    registry = get_provider_registry(request)
    return {
        'worker_registry_store': registry.get('worker_registry_store') is not None,
        'worker_profile_content_store': registry.get('worker_profile_content_store') is not None,
        'embedding_provider': registry.get('embedding_provider') is not None,
        'vector_store': registry.get('vector_store') is not None,
        'llm_gateway': registry.get('llm_gateway') is not None,
        'fusion_service': registry.get('fusion_service') is not None,
        'recommend_service': registry.get('recommend_service') is not None,
        'search_service': registry.get('search_service') is not None,
    }


__all__ = [
    'get_app_context',
    'get_provider_registry',
    'get_provider',
    'get_worker_store',
    'get_profile_store',
    'get_embedding_provider',
    'get_vector_store',
    'get_llm_gateway',
    'get_fusion_service',
    'get_recommend_service',
    'get_search_service',
    'check_provider_availability',
]