"""Service API Protocol of genome promotion (RSI-03).

Re-export only; the Protocol lives in ``core/bot_genome/promotion/`` so the
concrete service can inherit it without a ``core -> api`` waiver.
"""
from __future__ import annotations

from agentclaw.community.core.bot_genome.promotion.promotion_service_protocol import (
    GenomePromotionServiceProtocol,
)

__all__ = ["GenomePromotionServiceProtocol"]
