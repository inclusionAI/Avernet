"""Scope helpers — re-export from fastapi-injector.

``request_scope`` is the decorator form (``@request_scope`` on a
``@provider`` makes the binding live for one HTTP request).
``RequestScope`` is the scope class itself, useful when binding
imperatively via ``binder.bind(X, to=Y, scope=RequestScope)``.
"""
from fastapi_injector import RequestScope, request_scope


__all__ = ["RequestScope", "request_scope"]
