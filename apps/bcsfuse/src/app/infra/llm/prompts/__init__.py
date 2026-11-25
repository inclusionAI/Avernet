"""
LLM Prompts Package
"""

from app.infra.llm.prompts.fusion_recommendation_prompt import (
    FusionRecommendationPrompt,
    build_fusion_recommendation_prompt,
)

__all__ = [
    "FusionRecommendationPrompt",
    "build_fusion_recommendation_prompt",
]