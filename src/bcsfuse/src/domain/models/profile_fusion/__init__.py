"""
Profile Fusion 模型

G9 模式专用的数据模型。
"""

from src.domain.models.profile_fusion.fused_profile import (
    FusedProfile,
    ExpertProfile,
)
from src.domain.models.profile_fusion.fused_profile_record import (
    FusedProfileRecord,
)
from src.domain.models.profile_fusion.fusion_conversation import (
    ConversationTurn,
    ConversationStats,
)
from src.domain.models.profile_fusion.group_conversation_summary import (
    GroupConversationSummary,
)
from src.domain.models.profile_fusion.fusion_context import (
    FusionContext,
)

__all__ = [
    "FusedProfile",
    "ExpertProfile",
    "FusedProfileRecord",
    "ConversationTurn",
    "ConversationStats",
    "GroupConversationSummary",
    "FusionContext",
]