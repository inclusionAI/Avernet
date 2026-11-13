"""Compatibility combined Method API assembled from separate Single/Group operations."""

from agentclaw.community.core.task.context_collab.l5_methods.operations.group import (
    GroupOperations,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.single import (
    SingleOperations,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.single_group_base import (
    SingleGroupServiceBase,
)


class SingleGroupMethodService(
    SingleOperations, GroupOperations, SingleGroupServiceBase
):
    """Preserves the existing combined protocol and constructor without mixing implementations."""
