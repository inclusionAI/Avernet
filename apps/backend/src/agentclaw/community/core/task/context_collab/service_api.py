"""Compatibility reexports for the layer-owned L4 and L6 Service APIs.

Primitive commands/results and direct operations belong to L4. The legacy
combined creation command and extended service contract belong to L6. These
aliases preserve existing HTTP/library imports and object identity.
"""

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext as ActorContext,  # 兼容再导出:权威居所在 G1;新代码直接导入。
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ColletCreateCommand as ColletCreateCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextCollabStatusProjection as ContextCollabStatusProjection,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    EventAcceptedResult as EventAcceptedResult,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    EventAppendCommand as EventAppendCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    TaskCreateCommand as TaskCreateCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    TaskProgressProjectionDTO as TaskProgressProjectionDTO,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    WorkStartCommand as WorkStartCommand,
)
from agentclaw.community.core.task.context_collab.l6_products.service_api import (
    CollaborationCreateCommand as CollaborationCreateCommand,
)
from agentclaw.community.core.task.context_collab.l6_products.service_api import (
    ContextCollabServiceProtocol as ContextCollabServiceProtocol,
)
