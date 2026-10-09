"""Worker/Profile compatibility routes: common."""

import logging
from fastapi import APIRouter, Request


logger = logging.getLogger(__name__)

api_router = APIRouter()
mgmt_router = APIRouter()
admin_router = APIRouter()
compat_router = APIRouter()


def _get_worker_registry_store(request: Request):
    """Get worker registry store from provider registry."""
    return request.app.state.context.registry.get('worker_registry_store')


def _get_profile_content_store(request: Request):
    """Get profile content store from provider registry."""
    return request.app.state.context.registry.get('worker_profile_content_store')


def _get_profile_binding_store(request: Request):
    """Get profile binding store from provider registry."""
    return request.app.state.context.registry.get('worker_profile_binding_store')


def _get_runtime_state_store(request: Request):
    """Get runtime state store from provider registry."""
    return request.app.state.context.registry.get('worker_runtime_state_store')


def _get_audit_log_store(request: Request):
    """Get worker audit log store from provider registry."""
    return request.app.state.context.registry.get('worker_audit_log_store')


def _require_auth(request: Request) -> None:
    """Require authentication for protected endpoints."""
    from src.bootstrap.oss_business_routes import require_oss_auth
    require_oss_auth(request)
