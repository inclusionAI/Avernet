"""Auth — authentication and authorization accessor.

The active auth plugin is lazily discovered via entry points on first
access. Community ships a stub that always returns a hardcoded user;
enterprise delegates to the buservice API.
"""

from __future__ import annotations

from gateway.community.plugin_accessor import PluginAccessor
from gateway.community.plugins.auth.bare import BareAuthPlugin
from gateway.community.spi.auth import AuthPlugin

_accessor = PluginAccessor[AuthPlugin]("gateway.auth", BareAuthPlugin)


def get_auth_plugin() -> AuthPlugin:
    return _accessor.get()


def set_auth_plugin(plugin: AuthPlugin) -> None:
    _accessor.set(plugin)


__all__ = ["get_auth_plugin", "set_auth_plugin"]
