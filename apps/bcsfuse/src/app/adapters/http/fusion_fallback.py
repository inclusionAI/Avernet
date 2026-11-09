"""Deterministic response projection when TC has no enabled model."""

from app.adapters.http.schemas.fusion_schemas import FusionPerspective


def _extract_profile_summary(profile_content: dict | None) -> str:
    if not profile_content:
        return "No profile content available."
    description = profile_content.get("description")
    return (
        description
        if isinstance(description, str) and description.strip()
        else "No profile content available."
    )


def _create_minimal_perspective(
    participant_id: str, profile_content: dict | None, question: str
) -> FusionPerspective:
    summary = _extract_profile_summary(profile_content)
    return FusionPerspective(
        participant_id=participant_id,
        profile_key=participant_id,
        perspective=f"Current participant description: {summary}. Question: '{question[:100]}...'",
        confidence=0.5,
    )
