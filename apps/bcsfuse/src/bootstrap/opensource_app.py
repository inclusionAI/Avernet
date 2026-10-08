"""Open-source BCSFuse composition wrapper."""

from fastapi import FastAPI

from src.bootstrap.app_factory import create_bcsfuse_app
from src.bootstrap.application_context import build_application_context


def create_opensource_app(mode: str | None = None) -> FastAPI:
    """Build public providers and create the open-source application."""
    context = build_application_context(mode=mode)
    app = create_bcsfuse_app(context)
    app.title = "BCSFuse OSS"
    app.description = "BCSFuse Open Source Deployment"
    return app
