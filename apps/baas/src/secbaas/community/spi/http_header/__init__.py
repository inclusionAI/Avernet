"""HTTP header SPI — pluggable outbound header injection.

Provides the ``HttpHeaderPlugin`` protocol. The active header plugin is
managed via ``secbaas.community.http_header`` (default: no-op).
"""

from ._protocols import HttpHeaderPlugin

__all__ = ["HttpHeaderPlugin"]
