"""Local OpenClaw test double.

`plugins/local/` is for deterministic tests/contracts only.  It does not talk to
an OpenClaw gateway, spawn shells, run mcporter, or touch company infra.
"""
from engine.community.local.openclaw.plugin_impl import LocalOpenClawPluginImpl, OpenClawPluginImpl

__all__ = ["LocalOpenClawPluginImpl", "OpenClawPluginImpl"]
