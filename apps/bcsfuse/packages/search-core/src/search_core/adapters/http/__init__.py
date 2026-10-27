"""Optional HTTP delivery, explicitly wired with business and authentication."""

from search_core.adapters.http.routes import create_search_router

__all__ = ["create_search_router"]
