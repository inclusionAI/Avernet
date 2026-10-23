"""The compiler test rig: a session over the shared fakes, and a resolver over a seeded object store."""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.bot_config_manifest.apply.source_resolver import DeclaredSourceResolver
from agentclaw.community.core.bot_config_manifest.compiler.session import CompileSession
from tests.community.core.bot_config_manifest.apply._fakes import (
    FakeManifestContent,
    FakeObjectCredentials,
    declared_session,
    make_context,
    seeded_object_store,
)


def rig(objects: dict[str, bytes] | None = None, *, sources: dict[str, Any] | None = None, **context):
    """``(session, content, resolver)`` over an object store holding ``objects``."""
    content = FakeManifestContent()
    resolver = DeclaredSourceResolver(content, FakeObjectCredentials(), seeded_object_store(objects or {}))
    session = CompileSession.open(make_context(**context), source_session=declared_session(sources=sources or {}))
    return session, content, resolver
