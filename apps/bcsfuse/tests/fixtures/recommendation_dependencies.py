"""Contract-shaped vector dependencies for candidate recommendation tests."""

from unittest.mock import Mock

from src.application.services.worker_vector_match_service import MatchResult
from src.domain.models.metadata_record import MetadataRecord


def vector_dependencies(profiles):
    """Return deterministic vector matches respecting exclusions and top-k."""
    matcher = Mock()
    embedding = Mock()
    embedding.embed.return_value = [0.1, 0.2, 0.3]

    def match(query_embedding, top_k, excluded_profile_keys=None, **kwargs):
        excluded = set(excluded_profile_keys or [])
        return [
            MatchResult(
                profile_key=profile.profile_key,
                score=0.9,
                metadata=MetadataRecord(
                    profile_key=profile.profile_key,
                    staff_id=profile.staff_id,
                    profile_id=profile.profile_id,
                    profile_type=profile.profile_type.value,
                    source_root=profile.source_root,
                    active_skill_names=[skill.name for skill in profile.active_skills],
                ),
            )
            for profile in profiles if profile.profile_key not in excluded
        ][:top_k]

    matcher.match.side_effect = match
    return {"vector_match_service": matcher, "embedding_generator": embedding}
