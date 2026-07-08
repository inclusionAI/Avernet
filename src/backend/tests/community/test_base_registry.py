"""Regression test: core.base.Base and plugins.models.Base must be the same object.

If they differ, SQLAlchemy maintains two separate mapper registries and
cross-model relationships (e.g. SkillSet -> SkillSetMCPServer) fail at startup.
"""


def test_base_is_same_object():
    from agentclaw.community.core.base import Base as CoreBase
    from agentclaw.community.plugin_api.models import Base as PluginsBase
    assert CoreBase is PluginsBase, (
        "core.base.Base and plugins.models.Base are different objects — "
        "this causes a dual SQLAlchemy registry and breaks cross-model relationships."
    )


def test_base_has_single_registry():
    """All models that import Base from either path share one registry."""
    from agentclaw.community.core.base import Base as CoreBase
    from agentclaw.community.plugin_api.models import Base as PluginsBase
    assert CoreBase.registry is PluginsBase.registry
