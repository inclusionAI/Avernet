"""Five-method product Service API; progress ingress is a separate L5 capability."""

from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    AdjustTypeEnum,
)
from agentclaw.community.core.task.context_collab.l5_methods.collet_rollout import (
    ColletRollout,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    MethodCompositionTemplate,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile import (
    ProductProfile,
    YAML,
)


@dataclass(frozen=True)
class InteractiveResult:
    type: AdjustTypeEnum
    message: str


@dataclass(frozen=True)
class StageRolloutDetails:
    stage_rollout_id: int
    session_id: str
    messages: list[Any]
    bound: bool
    limit: int
    order: str = "provider-defined"
    complete: bool | None = None


@runtime_checkable
class ProductProfileServiceProtocol(Protocol):
    """No context/request_id parameters; trusted entrypoints bind the G1 actor scope.

    Creates are independent invocations. Interactive writes replay the result
    for the same actor/scoped interactive_uuid and identical rollout/content;
    changed content or rollout under that UUID conflicts. Revision CAS is internal.
    """

    @abstractmethod
    async def create_product_profile(
        self,
        name: str,
        composition: MethodCompositionTemplate,
        stages: YAML,
    ) -> ProductProfile: ...
    @abstractmethod
    async def create_product_collet(self, profile_id: int, content: str) -> Collet: ...
    @abstractmethod
    async def query_collet_rollout(
        self, collet_id: int, all_rollout: bool = True
    ) -> list[ColletRollout]: ...
    @abstractmethod
    async def interactive(
        self,
        collet_rollout_id: int,
        interactive_uuid: str,
        interactive_context: str,
    ) -> InteractiveResult: ...
    @abstractmethod
    async def query_stage_rollout_details(
        self, stage_rollout_id: int, *, limit: int = 50
    ) -> StageRolloutDetails: ...


class RolloutList(list[ColletRollout]):
    """List result with transport/control metadata, not extra domain fields."""

    def __init__(
        self,
        values: list[ColletRollout],
        revision: int,
        current_rollout_id: int,
        stopped: bool,
    ) -> None:
        super().__init__(values)
        self.revision = revision
        self.current_rollout_id = current_rollout_id
        self.stopped = stopped
