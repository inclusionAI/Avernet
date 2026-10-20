"""The genome graph resolves on the test profile, and the state reader stays a thunk until first use."""
from __future__ import annotations

from typing import Callable

from agentclaw.community.api.bot_genome_promotion_service import GenomePromotionServiceProtocol
from agentclaw.community.api.bot_genome_registry_service import GenomeRegistryProtocol
from agentclaw.community.core.bot_genome.capture import BotStateReader
from agentclaw.community.core.bot_genome.promotion import GenomePromotionService, PendingDeliveryApplier, RevisionApplier
from agentclaw.community.core.bot_genome.registry import GenomeRegistryService
from agentclaw.community.di.container import build_injector
from agentclaw.community.di.profile import DeployProfile


def test_both_services_resolve_and_share_one_registry() -> None:
    injector = build_injector(profile=DeployProfile.TEST)
    registry = injector.get(GenomeRegistryProtocol)
    promotion = injector.get(GenomePromotionServiceProtocol)
    assert isinstance(registry, GenomeRegistryService)
    assert isinstance(promotion, GenomePromotionService)
    assert promotion._registry is registry
    assert isinstance(injector.get(RevisionApplier), PendingDeliveryApplier)


def test_the_state_reader_is_a_thunk_that_builds_the_platform_reader() -> None:
    injector = build_injector(profile=DeployProfile.TEST)
    thunk = injector.get(Callable[[], BotStateReader])
    assert callable(thunk)
    reader = thunk()
    assert reader.refusal.__self__.__class__.__name__ == "PlatformBotStateReader"
