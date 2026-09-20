"""HTTP header — outbound header injection factory.

The active header plugin is resolved lazily on first access; deployments
replace it via ``set_http_header_plugin`` (default: no-op).

Usage::

    from secbaas.community.http_header import get_http_header_plugin

    headers: dict[str, str] = {}
    get_http_header_plugin().inject_header(url, headers)
"""

from __future__ import annotations

from secbaas.community.plugin_accessor import PluginAccessor
from secbaas.community.plugins.http_header.default import NoOpHttpHeaderPlugin
from secbaas.community.spi.http_header import HttpHeaderPlugin

_accessor = PluginAccessor[HttpHeaderPlugin](
    "secbaas.http_header", NoOpHttpHeaderPlugin
)


def get_http_header_plugin() -> HttpHeaderPlugin:
    return _accessor.get()


def set_http_header_plugin(plugin: HttpHeaderPlugin) -> None:
    _accessor.set(plugin)


__all__ = ["get_http_header_plugin", "set_http_header_plugin"]
