"""Worker Vector Index Service.

This service provides a unified interface for indexing and searching worker profiles
using both vector similarity and metadata filtering.

Design:
- Query pipeline:
  1. metadata filter (by MetadataStore)
  2. vector ANN search (by VectorStore)
  3. business rerank (optional, by application layer)

- This service coordinates between VectorStore and MetadataStore
- Embedding generation is pluggable (can be passed in or set)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from src.domain.models.metadata_record import MetadataRecord
from src.domain.models.vector_point import VectorPoint
from src.domain.models.vector_search_hit import VectorSearchHit
from src.domain.models.worker_profile import WorkerProfile
from src.domain.services.vector_store_adapter import VectorStoreAdapter
from src.domain.services.metadata_store_adapter import MetadataStoreAdapter


@dataclass
class IndexResult:
    """Result of indexing operation.

    Attributes:
        indexed_count: Number of successfully indexed profiles
        failed_count: Number of failed profiles
        failed_keys: List of failed profile keys
    """
    indexed_count: int = 0
    failed_count: int = 0
    failed_keys: list[str] = field(default_factory=list)


@dataclass
class SearchResult:
    """Single search result with metadata and score.

    Attributes:
        profile_key: Unique profile identifier
        score: Similarity score
        metadata: Full metadata record
    """
    profile_key: str
    score: float
    metadata: MetadataRecord


class WorkerVectorIndexService:
    """
    Service for indexing and searching worker profiles.

    This service provides:
    - Profile indexing with embeddings
    - Vector similarity search with metadata filtering
    - Metadata-only filtering
    - Persistence (save/load)

    Responsibilities:
    - Coordinate between VectorStore and MetadataStore
    - Convert WorkerProfile to VectorPoint and MetadataRecord
    - Maintain consistency between vector and metadata stores

    Non-responsibilities:
    - Generating embeddings (embedding function is injected)
    - Business recommendation logic
    - Complex reranking strategies

    Example:
        >>> service = WorkerVectorIndexService(vector_store, metadata_store)
        >>> service.set_embedding_function(my_embedding_fn)
        >>> service.index_profiles(profiles, embeddings)
        >>> results = service.search_by_vector(query_vector, top_k=10)
    """

    def __init__(
        self,
        vector_store: VectorStoreAdapter,
        metadata_store: MetadataStoreAdapter,
    ):
        """
        Initialize WorkerVectorIndexService.

        Args:
            vector_store: Vector store adapter for similarity search
            metadata_store: Metadata store adapter for filtering
        """
        self._vector_store = vector_store
        self._metadata_store = metadata_store
        self._embedding_fn: Callable[[str], list[float]] | None = None

    @property
    def vector_store(self) -> VectorStoreAdapter:
        """Get the vector store."""
        return self._vector_store

    @property
    def metadata_store(self) -> MetadataStoreAdapter:
        """Get the metadata store."""
        return self._metadata_store

    def set_embedding_function(self, fn: Callable[[str], list[float]]) -> None:
        """
        Set the embedding function for text-to-vector conversion.

        Args:
            fn: Function that takes text and returns embedding vector
        """
        self._embedding_fn = fn

    def index_profiles(
        self,
        profiles: list[WorkerProfile],
        embeddings: list[list[float]],
        domain_hints: list[list[str]] | None = None,
    ) -> IndexResult:
        """
        Index worker profiles with their embeddings.

        Args:
            profiles: List of worker profiles to index
            embeddings: Corresponding embeddings (same order as profiles)
            domain_hints: Optional domain hints for each profile

        Returns:
            IndexResult with counts and any failures

        Raises:
            ValueError: If profile and embedding counts don't match
        """
        if len(profiles) != len(embeddings):
            raise ValueError(
                f"Profile and embedding count mismatch: "
                f"{len(profiles)} profiles vs {len(embeddings)} embeddings"
            )

        if not profiles:
            return IndexResult()

        indexed_count = 0
        failed_keys = []

        # Prepare vector points and metadata records
        vector_points: list[VectorPoint] = []
        metadata_records: list[MetadataRecord] = []

        for i, (profile, embedding) in enumerate(zip(profiles, embeddings)):
            try:
                profile_key = profile.profile_key

                # Extract domains
                domains = domain_hints[i] if domain_hints and i < len(domain_hints) else []

                # Extract skill names
                active_skill_names = [s.name for s in profile.active_skills]

                # Extract suitable roles (placeholder, can be enhanced)
                suitable_roles = []

                # Create VectorPoint
                vector_points.append(VectorPoint(
                    id=profile_key,
                    vector=embedding,
                    payload={
                        "staff_id": profile.staff_id,
                        "profile_id": profile.profile_id,
                        "profile_type": profile.profile_type.value,
                    }
                ))

                # Create MetadataRecord
                metadata_records.append(MetadataRecord(
                    profile_key=profile_key,
                    staff_id=profile.staff_id,
                    profile_id=profile.profile_id,
                    profile_type=profile.profile_type.value,
                    domains=domains,
                    active_skill_names=active_skill_names,
                    suitable_roles=suitable_roles,
                    source_root=profile.source_root,
                ))

                indexed_count += 1

            except Exception as e:
                failed_keys.append(profile.profile_key)

        # Batch upsert to stores
        if vector_points:
            self._vector_store.upsert(vector_points)

        if metadata_records:
            # Update each metadata record with its vector_id
            for record in metadata_records:
                vector_id = self._vector_store.get_vector_id(record.profile_key)
                if vector_id is not None:
                    record.vector_id = vector_id

            self._metadata_store.upsert(metadata_records)

        return IndexResult(
            indexed_count=indexed_count,
            failed_count=len(failed_keys),
            failed_keys=failed_keys,
        )

    def search_by_vector(
        self,
        vector: list[float],
        top_k: int,
        metadata_filter: dict | None = None,
    ) -> list[SearchResult]:
        """
        Search for similar profiles by vector.

        Query pipeline:
        1. Apply metadata filter to get candidate profile_keys
        2. Perform vector search
        3. Combine results with metadata

        Args:
            vector: Query vector
            top_k: Maximum number of results
            metadata_filter: Optional metadata filter criteria

        Returns:
            List of SearchResult with profile metadata and scores
        """
        # If metadata filter is provided, first filter metadata
        candidate_keys: set[str] | None = None
        if metadata_filter:
            filtered_records = self._metadata_store.filter(metadata_filter)
            candidate_keys = {r.profile_key for r in filtered_records}

            # If no candidates, return empty
            if not candidate_keys:
                return []

        # Perform vector search
        # Note: FAISS doesn't support filters directly, so we filter post-hoc
        hits = self._vector_store.search(vector, top_k=top_k * 2 if candidate_keys else top_k)

        # Combine with metadata and filter
        results: list[SearchResult] = []
        for hit in hits:
            # Filter by candidate keys if provided
            if candidate_keys and hit.id not in candidate_keys:
                continue

            # Get metadata
            metadata = self._metadata_store.get(hit.id)
            if metadata is None:
                continue

            results.append(SearchResult(
                profile_key=hit.id,
                score=hit.score,
                metadata=metadata,
            ))

            if len(results) >= top_k:
                break

        return results

    def search_by_text(
        self,
        text: str,
        top_k: int,
        metadata_filter: dict | None = None,
    ) -> list[SearchResult]:
        """
        Search for similar profiles by text.

        Requires an embedding function to be set.

        Args:
            text: Query text
            top_k: Maximum number of results
            metadata_filter: Optional metadata filter criteria

        Returns:
            List of SearchResult with profile metadata and scores

        Raises:
            RuntimeError: If no embedding function is set
        """
        if self._embedding_fn is None:
            raise RuntimeError(
                "No embedding function set. Call set_embedding_function() first."
            )

        vector = self._embedding_fn(text)
        return self.search_by_vector(vector, top_k, metadata_filter)

    def filter_workers(self, filters: dict | None = None) -> list[MetadataRecord]:
        """
        Filter workers by metadata criteria.

        Args:
            filters: Filter criteria (domains, skills, roles, etc.)

        Returns:
            List of matching metadata records
        """
        return self._metadata_store.filter(filters)

    def get_metadata(self, profile_key: str) -> MetadataRecord | None:
        """
        Get metadata for a specific profile.

        Args:
            profile_key: Unique profile identifier

        Returns:
            MetadataRecord or None if not found
        """
        return self._metadata_store.get(profile_key)

    def delete_profiles(self, profile_keys: list[str]) -> None:
        """
        Delete profiles from both stores.

        Args:
            profile_keys: List of profile keys to delete
        """
        self._vector_store.delete(profile_keys)
        self._metadata_store.delete(profile_keys)

    def save(self, path: str) -> None:
        """
        Save both vector and metadata stores.

        Args:
            path: Directory path to save files
        """
        self._vector_store.save_snapshot(path)
        self._metadata_store.save(path)

    def load(self, path: str) -> None:
        """
        Load both vector and metadata stores.

        Args:
            path: Directory path containing saved files
        """
        self._vector_store.load_snapshot(path)
        self._metadata_store.load(path)

    def get_stats(self) -> dict[str, Any]:
        """
        Get statistics about the index.

        Returns:
            Dictionary with stats (total_profiles, total_vectors, etc.)
        """
        return {
            "total_profiles": self._metadata_store.size(),
            "total_vectors": self._vector_store.size(),
        }


__all__ = ["WorkerVectorIndexService", "IndexResult", "SearchResult"]