"""Real recommendation/vector/registry chain with fixed vector search results."""

from unittest.mock import Mock

from src.application.services.worker_candidate_recommendation_impl import WorkerCandidateRecommendationImpl
from src.application.services.worker_vector_match_service import (
    FragmentRetrievalConfig,
    WorkerVectorMatchService,
)
from src.domain.models.metadata_record import MetadataRecord
from src.domain.models.vector_search_hit import VectorSearchHit
from src.domain.services.metadata_store_adapter import MetadataStoreAdapter
from src.domain.services.vector_store_adapter import VectorStoreAdapter


def registry_recommendation_service(retrieval_service, profile_filter, profiles, min_experts=3):
    """Keep registry filtering real even when ANN returns ineligible candidates."""
    records = {
        profile.profile_key: MetadataRecord(
            profile_key=profile.profile_key,
            staff_id=profile.staff_id,
            profile_id=profile.profile_id,
            profile_type=profile.profile_type.value,
            source_root=profile.source_root,
            active_skill_names=[skill.name for skill in profile.active_skills],
        )
        for profile in profiles
    }
    vector_store = Mock(spec=VectorStoreAdapter)
    vector_store.size.return_value = len(profiles)
    vector_store.search.return_value = [
        VectorSearchHit(id=profile.profile_key, score=0.9) for profile in profiles
    ]
    metadata_store = Mock(spec=MetadataStoreAdapter)
    metadata_store.get.side_effect = records.get
    vector_service = WorkerVectorMatchService(
        vector_store, metadata_store, profile_filter=profile_filter,
        fragment_config=FragmentRetrievalConfig(enable_fragment_embedding=False),
    )
    embedding = Mock()
    embedding.embed.return_value = [1.0, 0.0, 0.0]
    return WorkerCandidateRecommendationImpl(
        retrieval_service=retrieval_service,
        vector_match_service=vector_service,
        embedding_generator=embedding,
        min_experts=min_experts,
    )
