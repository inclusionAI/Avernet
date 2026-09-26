"""Test doubles for callers whose fixtures are already Runtime-ready."""

from __future__ import annotations


class PassthroughSkillVersionResolver:
    """Preserve explicitly prepared assets in tests outside version resolution."""

    def resolve_latest_runtime_assets(self, *, env, assets):
        return tuple(assets)

    def resolve_exact_published(self, **kwargs):  # pragma: no cover - misuse guard
        raise AssertionError(kwargs)


__all__ = ["PassthroughSkillVersionResolver"]
