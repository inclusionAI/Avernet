"""Plugin marker Protocol.

Every plugin Protocol under ``agentclaw.community.plugin_api.*`` extends ``Plugin``.
This makes the set of plugin Protocols discoverable (walk modules,
``issubclass(X, Plugin)``) so the Rule 20/21 arch test can enforce:

- Rule 20 — every Plugin has ≥1 local + ≥1 prod impl
- Rule 21 — every local impl declares an isolation ``Flavor``

PEP 544 quirk: a Protocol that inherits from another Protocol MUST list
``Protocol`` in its own bases too, or it stops being a Protocol. So
plugin Protocols spell themselves as ``class FooPlugin(Plugin, Protocol)``.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Plugin(Protocol):
    """Marker base for plugin Protocols. No methods."""
    ...
