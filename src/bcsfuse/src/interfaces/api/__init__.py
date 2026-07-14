"""
API Module

HTTP/REST API 接口。

M1: Worker Registry API
"""

from src.interfaces.api.app import app
from src.interfaces.api.worker_routes import router as worker_router

__all__ = [
    "app",
    "worker_router",
]