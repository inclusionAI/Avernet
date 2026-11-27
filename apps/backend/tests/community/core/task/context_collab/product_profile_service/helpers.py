"""Seed persisted custom/legacy profiles, not a public composition-creation API."""

from uuid import uuid4

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    current_actor_context,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    validate_configuration,
)


def persist_profile(h, name, composition, stages):
    validate_configuration(composition, stages, h.execution)
    return h.repository.create_profile(
        name, composition, stages, current_actor_context(), uuid4().hex
    )
