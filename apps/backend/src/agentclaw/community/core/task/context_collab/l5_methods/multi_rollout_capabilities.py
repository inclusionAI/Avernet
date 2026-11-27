"""Fail-closed public defaults and deterministic, evaluator-backed selection."""

from ..errors import ContextCollabValidationError as Invalid
from .multi_rollout_policy import ParallelBestOf
from .multi_rollout_contracts import StrategyDescriptor


class UnavailableMultiRolloutStrategy:
    def require(self, policy, stages, context):
        raise Invalid(
            "MultiRollout strategy/evaluator/delegation capability is not installed"
        )

    async def evaluate(self, *args):
        raise Invalid("trusted MultiRollout evaluator is not installed")

    async def propose_adjustment(self, *args):
        raise Invalid("MultiRollout adaptation agent is not installed")


class UnavailableMultiRolloutIsolation:
    def require(self, *args):
        raise Invalid(
            "MultiRollout isolation and safe side-effect capability is not installed"
        )

    async def prepare(self, *args):
        raise Invalid("MultiRollout isolation is not installed")

    async def verify_route(self, *args):
        raise Invalid("MultiRollout route isolation is not installed")

    async def verify_artifacts(self, *args):
        raise Invalid("MultiRollout artifact isolation is not installed")


class UnavailableMultiRolloutNotifications:
    def require(self):
        raise Invalid("idempotent feedback notification capability is not installed")

    async def notify(self, *args):
        raise Invalid("feedback notification capability is not installed")


class HighestScoreStrategy:
    """Local selection contract; trusted scoring remains a host capability.

    evaluator.require(context) must authorize artifact access and return its
    exact version; evaluate receives the SAME immutable rubric for every run.
    It is deliberately not a worker-output JSON score parser.
    """

    def __init__(self, evaluator, rubric: str, minimum: int, maximum: int):
        self.evaluator = evaluator
        self.rubric, self.minimum, self.maximum = rubric, minimum, maximum

    def require(self, policy, stages, context):
        if (
            not isinstance(policy.strategy, ParallelBestOf)
            or policy.strategy.selection_ref != "highest-score@1"
        ):
            raise Invalid("highest-score@1 only supports parallel selection")
        evaluator_ref = self.evaluator.require(context)
        return StrategyDescriptor(
            "highest-score@1", self.rubric, evaluator_ref, self.minimum, self.maximum
        )

    async def evaluate(self, descriptor, snapshot, qualified, context):
        if descriptor != StrategyDescriptor(
            "highest-score@1",
            self.rubric,
            self.evaluator.require(context),
            self.minimum,
            self.maximum,
        ):
            raise Invalid("installed evaluation rubric/version changed")
        return await self.evaluator.evaluate(descriptor, snapshot, qualified, context)

    async def propose_adjustment(self, *args):
        raise Invalid("no adaptation agent installed by highest-score@1")
