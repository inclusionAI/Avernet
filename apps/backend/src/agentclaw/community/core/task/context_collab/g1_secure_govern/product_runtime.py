"""Default product runtime G1 hook. Eligibility is never an execution grant."""

from agentclaw.community.core.errors import Forbidden
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)


class OwnerProductRuntimeAuthorization:
    def check(self, action, context, owner):
        PrincipalRef.from_dict(context.actor.to_dict())
        if action == "report":
            # Precise allocation/attempt and acceptance authority are checked
            # by the ingress and trusted validator before any commit.
            return
        if context.actor != owner:
            raise Forbidden("product operation requires its recorded owner")

    def check_candidate(self, candidate, context, snapshot):
        raise Forbidden(
            "actual candidate participation/resource authority is not configured"
        )
