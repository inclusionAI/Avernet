"""Compatibility reexports; Method operations are L5, product recovery is L6."""

from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    top_level_binding_id as top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    _now as _now,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    _shape_of as _shape_of,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes as current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.method_bootstrap import (
    ensure_top_level_binding as ensure_top_level_binding,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.method_bootstrap import (
    project_created_context as project_created_context,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.method_lifecycle import (
    MethodLifecycleService as MethodLifecycleService,
)
