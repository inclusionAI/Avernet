"""Tests for VectorSearchHit domain model."""

import pytest
from pydantic import ValidationError

from src.domain.models.vector_search_hit import VectorSearchHit


class TestVectorSearchHit:
    """Test VectorSearchHit model."""

    def test_create_search_hit_with_required_fields(self):
        """Test creating VectorSearchHit with only required fields."""
        hit = VectorSearchHit(
            id="worker_001",
            score=0.95
        )

        assert hit.id == "worker_001"
        assert hit.score == 0.95
        assert hit.payload == {}

    def test_create_search_hit_with_payload(self):
        """Test creating VectorSearchHit with payload."""
        hit = VectorSearchHit(
            id="worker_001",
            score=0.95,
            payload={"staff_id": "staff_123", "profile_id": "default"}
        )

        assert hit.id == "worker_001"
        assert hit.score == 0.95
        assert hit.payload == {"staff_id": "staff_123", "profile_id": "default"}

    def test_missing_id_raises_error(self):
        """Test that missing id raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            VectorSearchHit(score=0.95)

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("id",)
        assert errors[0]["type"] == "missing"

    def test_missing_score_raises_error(self):
        """Test that missing score raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            VectorSearchHit(id="worker_001")

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("score",)
        assert errors[0]["type"] == "missing"

    def test_score_can_be_zero(self):
        """Test that score can be zero (edge case)."""
        hit = VectorSearchHit(
            id="worker_001",
            score=0.0
        )

        assert hit.id == "worker_001"
        assert hit.score == 0.0

    def test_score_can_be_one(self):
        """Test that score can be 1.0 (perfect match)."""
        hit = VectorSearchHit(
            id="worker_001",
            score=1.0
        )

        assert hit.id == "worker_001"
        assert hit.score == 1.0

    def test_score_can_be_negative(self):
        """Test that score can be negative (for certain distance metrics)."""
        hit = VectorSearchHit(
            id="worker_001",
            score=-0.5
        )

        assert hit.id == "worker_001"
        assert hit.score == -0.5

    def test_score_can_be_greater_than_one(self):
        """Test that score can be greater than 1.0 (for certain distance metrics)."""
        hit = VectorSearchHit(
            id="worker_001",
            score=2.5
        )

        assert hit.id == "worker_001"
        assert hit.score == 2.5

    def test_score_must_be_numeric(self):
        """Test that score must be numeric."""
        # Valid: float
        hit1 = VectorSearchHit(id="worker_001", score=0.95)
        assert hit1.score == 0.95

        # Valid: int (should be converted to float)
        hit2 = VectorSearchHit(id="worker_001", score=1)
        assert hit2.score == 1.0
        assert isinstance(hit2.score, float)

    def test_payload_can_be_any_dict(self):
        """Test that payload can contain various types."""
        hit = VectorSearchHit(
            id="worker_001",
            score=0.95,
            payload={
                "string": "value",
                "int": 123,
                "float": 0.5,
                "bool": True,
                "list": ["a", "b", "c"],
                "nested": {"key": "value"}
            }
        )

        assert hit.payload["string"] == "value"
        assert hit.payload["int"] == 123
        assert hit.payload["float"] == 0.5
        assert hit.payload["bool"] is True
        assert hit.payload["list"] == ["a", "b", "c"]
        assert hit.payload["nested"] == {"key": "value"}

    def test_extra_fields_forbidden(self):
        """Test that extra fields are forbidden."""
        with pytest.raises(ValidationError) as exc_info:
            VectorSearchHit(
                id="worker_001",
                score=0.95,
                extra_field="not_allowed"
            )

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["type"] == "extra_forbidden"

    def test_payload_default_is_empty_dict(self):
        """Test that payload defaults to empty dict."""
        hit = VectorSearchHit(
            id="worker_001",
            score=0.95
        )

        assert hit.payload == {}
        assert isinstance(hit.payload, dict)

    def test_search_hit_serialization(self):
        """Test that VectorSearchHit can be serialized to dict."""
        hit = VectorSearchHit(
            id="worker_001",
            score=0.95,
            payload={"key": "value"}
        )

        hit_dict = hit.model_dump()

        assert hit_dict["id"] == "worker_001"
        assert hit_dict["score"] == 0.95
        assert hit_dict["payload"] == {"key": "value"}

    def test_search_hit_deserialization(self):
        """Test that VectorSearchHit can be created from dict."""
        data = {
            "id": "worker_001",
            "score": 0.95,
            "payload": {"key": "value"}
        }

        hit = VectorSearchHit(**data)

        assert hit.id == "worker_001"
        assert hit.score == 0.95
        assert hit.payload == {"key": "value"}

    def test_multiple_search_hits_sorted_by_score(self):
        """Test that multiple hits can be sorted by score."""
        hits = [
            VectorSearchHit(id="worker_003", score=0.75),
            VectorSearchHit(id="worker_001", score=0.95),
            VectorSearchHit(id="worker_002", score=0.85),
        ]

        sorted_hits = sorted(hits, key=lambda h: h.score, reverse=True)

        assert sorted_hits[0].id == "worker_001"
        assert sorted_hits[1].id == "worker_002"
        assert sorted_hits[2].id == "worker_003"

    def test_search_hit_with_very_high_score(self):
        """Test search hit with very high score (edge case)."""
        hit = VectorSearchHit(
            id="worker_001",
            score=999.999
        )

        assert hit.score == 999.999

    def test_search_hit_with_very_low_score(self):
        """Test search hit with very low score (edge case)."""
        hit = VectorSearchHit(
            id="worker_001",
            score=-999.999
        )

        assert hit.score == -999.999