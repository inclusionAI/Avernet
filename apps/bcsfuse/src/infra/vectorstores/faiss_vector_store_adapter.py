"""FAISS-based Vector Store Adapter implementation.

This module provides a local FAISS-based implementation of VectorStoreAdapter,
using IndexFlatIP (inner product) for similarity search with normalized vectors.
"""

import json
import os
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from src.domain.models.vector_point import VectorPoint
from src.domain.models.vector_search_hit import VectorSearchHit
from src.domain.services.vector_store_adapter import VectorStoreAdapter


class FaissVectorStoreAdapter:
    """
    FAISS-based implementation of VectorStoreAdapter.

    Key Features:
    - Uses IndexFlatIP (inner product) for similarity search
    - Automatically normalizes vectors for cosine similarity
    - Maintains string id <-> faiss internal row mapping
    - Supports save/load snapshots

    Delete Strategy:
    - Uses logical deletion with ID mapping
    - Deleted IDs are marked in id_map but not removed from FAISS index
    - On search, deleted IDs are filtered out
    - On save/load, deleted entries are preserved in mapping

    Note:
    - This is a local baseline implementation
    - For production, consider using Qdrant, Milvus, or other distributed solutions
    - The filters parameter is accepted but not used (filtering should be done at metadata layer)

    Attributes:
        dimension: Vector dimension
    """

    def __init__(self, dimension: int):
        """
        Initialize FaissVectorStoreAdapter.

        Args:
            dimension: Dimension of vectors to be stored
        """
        self._dimension = dimension
        self._index: faiss.IndexFlatIP | None = None
        self._id_to_row: dict[str, int] = {}  # string id -> FAISS row index
        self._row_to_id: dict[int, str] = {}  # FAISS row index -> string id
        self._id_to_payload: dict[str, dict[str, Any]] = {}  # string id -> payload
        self._deleted_ids: set[str] = set()  # Set of deleted IDs
        self._next_row: int = 0  # Next available row index

    @property
    def dimension(self) -> int:
        """Get the vector dimension."""
        return self._dimension

    def upsert(self, points: list[VectorPoint]) -> None:
        """
        Insert or update vector points.

        Args:
            points: List of vector points to upsert

        Raises:
            ValueError: If vector dimension doesn't match or points is empty after validation
        """
        if not points:
            return

        # Validate dimensions
        for point in points:
            if len(point.vector) != self._dimension:
                raise ValueError(
                    f"Vector dimension mismatch: expected {self._dimension}, "
                    f"got {len(point.vector)} for id '{point.id}'"
                )

        # Initialize index if needed
        if self._index is None:
            self._index = faiss.IndexFlatIP(self._dimension)

        for point in points:
            # Check if this ID already exists
            if point.id in self._id_to_row:
                # Update existing entry
                # Note: We can't truly update in FAISS, so we just update the mapping
                # The old vector remains in the index but will be filtered out by id
                row_idx = self._id_to_row[point.id]
                self._row_to_id[row_idx] = point.id
                self._id_to_payload[point.id] = point.payload
                self._deleted_ids.discard(point.id)  # Remove from deleted if present

                # Add new vector with new row index
                vector_np = self._normalize_vector(point.vector)
                self._index.add(vector_np)
                new_row_idx = self._next_row
                self._next_row += 1

                # Update mappings to point to new row
                self._id_to_row[point.id] = new_row_idx
                self._row_to_id[new_row_idx] = point.id
                # Old row mapping is kept but will have wrong id (filtered by deleted_ids logic)
                # Actually, we should mark the old row as deleted
                # For simplicity, we just add the new vector and update the mapping
                del self._row_to_id[row_idx]  # Remove old mapping

            else:
                # Add new entry
                vector_np = self._normalize_vector(point.vector)
                self._index.add(vector_np)

                row_idx = self._next_row
                self._next_row += 1

                self._id_to_row[point.id] = row_idx
                self._row_to_id[row_idx] = point.id
                self._id_to_payload[point.id] = point.payload
                self._deleted_ids.discard(point.id)

    def delete(self, ids: list[str]) -> None:
        """
        Delete vector points by IDs.

        Note: This uses logical deletion. The vectors remain in the FAISS index
        but are marked as deleted and filtered out during search.

        Args:
            ids: List of vector IDs to delete
        """
        for id in ids:
            if id in self._id_to_row:
                self._deleted_ids.add(id)

    def search(
        self,
        vector: list[float],
        top_k: int,
        filters: dict | None = None,
    ) -> list[VectorSearchHit]:
        """
        Search for similar vectors.

        Args:
            vector: Query vector
            top_k: Maximum number of results
            filters: Optional filters (not used in this implementation)

        Returns:
            List of search hits sorted by similarity score (descending)

        Raises:
            ValueError: If index is empty or vector dimension doesn't match
        """
        if self._index is None or self._index.ntotal == 0:
            raise ValueError("Cannot search empty index")

        if len(vector) != self._dimension:
            raise ValueError(
                f"Vector dimension mismatch: expected {self._dimension}, "
                f"got {len(vector)}"
            )

        # Normalize query vector
        query_np = self._normalize_vector(vector)

        # Search with more candidates to account for deleted vectors
        # Use top_k * 3 to get enough candidates after filtering
        search_k = min(top_k * 3, self._index.ntotal)
        if search_k < top_k:
            search_k = top_k

        scores, indices = self._index.search(query_np, search_k)

        # Convert to hits, filtering out deleted and unmapped entries
        hits = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:  # FAISS returns -1 for no result
                continue

            id = self._row_to_id.get(int(idx))
            if id is None or id in self._deleted_ids:
                continue

            payload = self._id_to_payload.get(id, {})

            hits.append(VectorSearchHit(
                id=id,
                score=float(score),
                payload=payload,
            ))

            if len(hits) >= top_k:
                break

        return hits

    def batch_search(
        self,
        vectors: list[list[float]],
        top_k: int,
        filters: dict | None = None,
    ) -> list[list[VectorSearchHit]]:
        """
        Batch search for similar vectors.

        Args:
            vectors: List of query vectors
            top_k: Maximum number of results per query
            filters: Optional filters (not used in this implementation)

        Returns:
            List of search result lists

        Raises:
            ValueError: If index is empty or vector dimensions don't match
        """
        if self._index is None or self._index.ntotal == 0:
            raise ValueError("Cannot search empty index")

        # Validate all vectors
        for i, v in enumerate(vectors):
            if len(v) != self._dimension:
                raise ValueError(
                    f"Vector {i} dimension mismatch: expected {self._dimension}, "
                    f"got {len(v)}"
                )

        # Normalize all query vectors
        queries_np = np.array([self._normalize_vector(v)[0] for v in vectors])

        # Search with more candidates
        search_k = min(top_k * 3, self._index.ntotal)
        if search_k < top_k:
            search_k = top_k

        scores, indices = self._index.search(queries_np, search_k)

        # Convert results
        all_hits = []
        for query_scores, query_indices in zip(scores, indices):
            hits = []
            for score, idx in zip(query_scores, query_indices):
                if idx < 0:
                    continue

                id = self._row_to_id.get(int(idx))
                if id is None or id in self._deleted_ids:
                    continue

                payload = self._id_to_payload.get(id, {})

                hits.append(VectorSearchHit(
                    id=id,
                    score=float(score),
                    payload=payload,
                ))

                if len(hits) >= top_k:
                    break

            all_hits.append(hits)

        return all_hits

    def save_snapshot(self, path: str) -> None:
        """
        Save index and mappings to files.

        Creates:
        - index.faiss: FAISS index file
        - id_map.json: ID to row mapping
        - payload_map.json: ID to payload mapping

        Args:
            path: Directory path to save files

        Raises:
            IOError: If save fails
        """
        save_dir = Path(path)
        save_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Save FAISS index
            # Create empty index if none exists
            if self._index is None:
                self._index = faiss.IndexFlatIP(self._dimension)

            index_path = save_dir / "index.faiss"
            faiss.write_index(self._index, str(index_path))

            # Save mappings
            id_map_path = save_dir / "id_map.json"
            with open(id_map_path, "w", encoding="utf-8") as f:
                json.dump({
                    "id_to_row": self._id_to_row,
                    "row_to_id": {str(k): v for k, v in self._row_to_id.items()},
                    "deleted_ids": list(self._deleted_ids),
                    "next_row": self._next_row,
                    "dimension": self._dimension,
                }, f, indent=2)

            payload_map_path = save_dir / "payload_map.json"
            with open(payload_map_path, "w", encoding="utf-8") as f:
                json.dump(self._id_to_payload, f, indent=2)

        except Exception as e:
            raise IOError(f"Failed to save snapshot: {e}") from e

    def load_snapshot(self, path: str) -> None:
        """
        Load index and mappings from files.

        Args:
            path: Directory path containing snapshot files

        Raises:
            FileNotFoundError: If files don't exist
            IOError: If load fails
            ValueError: If dimension mismatch
        """
        load_dir = Path(path)

        if not load_dir.exists():
            raise FileNotFoundError(f"Directory not found: {path}")

        index_path = load_dir / "index.faiss"
        id_map_path = load_dir / "id_map.json"

        if not index_path.exists():
            raise FileNotFoundError(f"Index file not found: {index_path}")

        if not id_map_path.exists():
            raise FileNotFoundError(f"ID map file not found: {id_map_path}")

        try:
            # Load FAISS index
            self._index = faiss.read_index(str(index_path))

            # Load mappings
            with open(id_map_path, "r", encoding="utf-8") as f:
                id_map = json.load(f)

            self._id_to_row = id_map["id_to_row"]
            self._row_to_id = {int(k): v for k, v in id_map["row_to_id"].items()}
            self._deleted_ids = set(id_map["deleted_ids"])
            self._next_row = id_map["next_row"]
            saved_dimension = id_map["dimension"]

            # Validate dimension
            if saved_dimension != self._dimension:
                raise ValueError(
                    f"Dimension mismatch: adapter has dimension {self._dimension}, "
                    f"but snapshot has dimension {saved_dimension}"
                )

            # Load payloads
            payload_map_path = load_dir / "payload_map.json"
            if payload_map_path.exists():
                with open(payload_map_path, "r", encoding="utf-8") as f:
                    self._id_to_payload = json.load(f)
            else:
                self._id_to_payload = {}

        except json.JSONDecodeError as e:
            raise IOError(f"Invalid JSON in mapping file: {e}") from e
        except Exception as e:
            if isinstance(e, (FileNotFoundError, ValueError)):
                raise
            raise IOError(f"Failed to load snapshot: {e}") from e

    def size(self) -> int:
        """
        Get the number of active (non-deleted) vectors.

        Returns:
            Number of active vectors
        """
        return len(self._id_to_row) - len(self._deleted_ids)

    def text_search(
        self,
        query: str,
        top_k: int,
        filters: dict | None = None,
    ) -> list[VectorSearchHit]:
        """
        BM25 关键词搜索。

        基于 BM25 算法对存储在 payload 中的文本内容进行关键词检索。

        Args:
            query: 查询关键词字符串
            top_k: 返回结果数量
            filters: 可选过滤条件（当前未实现）

        Returns:
            搜索结果列表，按 BM25 分数降序排列

        Raises:
            NotImplementedError: BM25 搜索尚未实现
        """
        raise NotImplementedError("BM25 text search is not implemented yet")

    def batch_text_search(
        self,
        queries: list[str],
        top_k: int,
        filters: dict | None = None,
    ) -> list[list[VectorSearchHit]]:
        """
        批量 BM25 关键词搜索。

        Args:
            queries: 查询关键词列表
            top_k: 每个查询返回的结果数量
            filters: 可选过滤条件（当前未实现）

        Returns:
            搜索结果列表的列表

        Raises:
            NotImplementedError: BM25 搜索尚未实现
        """
        raise NotImplementedError("BM25 text search is not implemented yet")

    def update_payloads_by_worker_id(
        self,
        worker_id: str,
        payload_updates: dict,
    ) -> int:
        """
        Update payload fields for all vectors belonging to a worker.

        This updates the in-memory _id_to_payload dict only — the caller
        is responsible for persisting changes to the backend.

        Used by FaissSqliteVectorStore.update_payload_by_worker() to
        synchronise availability / runtime_state changes to vector payloads
        without re-embedding.

        Args:
            worker_id: Worker ID to match against payload["worker_id"]
            payload_updates: Dict of payload fields to merge (e.g. {"availability": "public"})

        Returns:
            Number of vectors updated
        """
        count = 0
        for vec_id, payload in self._id_to_payload.items():
            if vec_id not in self._deleted_ids and payload.get("worker_id") == worker_id:
                payload.update(payload_updates)
                count += 1
        return count

    def get_vector_ids(self) -> list[str]:
        """
        Get all active vector IDs.

        Returns:
            List of active vector IDs
        """
        return [
            id for id in self._id_to_row.keys()
            if id not in self._deleted_ids
        ]

    def get_vector_id(self, id: str) -> int | None:
        """
        Get the FAISS row index for a given vector ID.

        Args:
            id: Vector ID (string)

        Returns:
            FAISS row index (int) or None if not found or deleted
        """
        if id in self._deleted_ids:
            return None
        return self._id_to_row.get(id)

    def _normalize_vector(self, vector: list[float]) -> np.ndarray:
        """
        Normalize a vector for inner product search.

        This converts inner product to cosine similarity.

        Args:
            vector: Input vector

        Returns:
            Normalized vector as numpy array with shape (1, dimension)
        """
        vec_np = np.array(vector, dtype=np.float32).reshape(1, -1)
        norm = np.linalg.norm(vec_np)
        if norm > 0:
            vec_np = vec_np / norm
        return vec_np