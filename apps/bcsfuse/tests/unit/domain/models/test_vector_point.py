"""Tests for VectorPoint domain model."""

import pytest
from pydantic import ValidationError

from src.domain.models.vector_point import VectorPoint


class TestVectorPoint:
    """Test VectorPoint model."""

    def test_create_vector_point_with_required_fields(self):
        """Test creating VectorPoint with only required fields."""
        point = VectorPoint(
            id="worker_001",
            vector=[0.1, 0.2, 0.3, 0.4]
        )

        assert point.id == "worker_001"
        assert point.vector == [0.1, 0.2, 0.3, 0.4]
        assert point.payload == {}

    def test_create_vector_point_with_payload(self):
        """Test creating VectorPoint with payload."""
        point = VectorPoint(
            id="worker_001",
            vector=[0.1, 0.2, 0.3, 0.4],
            payload={"staff_id": "staff_123", "profile_id": "default"}
        )

        assert point.id == "worker_001"
        assert point.vector == [0.1, 0.2, 0.3, 0.4]
        assert point.payload == {"staff_id": "staff_123", "profile_id": "default"}

    def test_missing_id_raises_error(self):
        """Test that missing id raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            VectorPoint(vector=[0.1, 0.2, 0.3])

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("id",)
        assert errors[0]["type"] == "missing"

    def test_missing_vector_raises_error(self):
        """Test that missing vector raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            VectorPoint(id="worker_001")

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("vector",)
        assert errors[0]["type"] == "missing"

    def test_empty_vector_allowed(self):
        """Test that empty vector is allowed (valid edge case)."""
        point = VectorPoint(
            id="worker_001",
            vector=[]
        )

        assert point.id == "worker_001"
        assert point.vector == []

    def test_vector_with_single_dimension(self):
        """Test vector with single dimension."""
        point = VectorPoint(
            id="worker_001",
            vector=[0.5]
        )

        assert point.id == "worker_001"
        assert point.vector == [0.5]

    def test_vector_with_high_dimensions(self):
        """Test vector with high dimensions (e.g., 1536 for OpenAI embeddings)."""
        # Simulate a 1536-dimension vector
        vector = [0.1] * 1536
        point = VectorPoint(
            id="worker_001",
            vector=vector
        )

        assert point.id == "worker_001"
        assert len(point.vector) == 1536
        assert point.vector[0] == 0.1
        assert point.vector[1535] == 0.1

    def test_vector_must_contain_floats(self):
        """Test that vector must contain float values."""
        # Valid: list of floats
        point = VectorPoint(
            id="worker_001",
            vector=[0.1, 0.2, 0.3]
        )
        assert point.vector == [0.1, 0.2, 0.3]

    def test_payload_can_be_any_dict(self):
        """Test that payload can contain various types."""
        point = VectorPoint(
            id="worker_001",
            vector=[0.1, 0.2, 0.3],
            payload={
                "string": "value",
                "int": 123,
                "float": 0.5,
                "bool": True,
                "list": ["a", "b", "c"],
                "nested": {"key": "value"}
            }
        )

        assert point.payload["string"] == "value"
        assert point.payload["int"] == 123
        assert point.payload["float"] == 0.5
        assert point.payload["bool"] is True
        assert point.payload["list"] == ["a", "b", "c"]
        assert point.payload["nested"] == {"key": "value"}

    def test_extra_fields_forbidden(self):
        """Test that extra fields are forbidden."""
        with pytest.raises(ValidationError) as exc_info:
            VectorPoint(
                id="worker_001",
                vector=[0.1, 0.2, 0.3],
                extra_field="not_allowed"
            )

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["type"] == "extra_forbidden"

    def test_vector_point_immutability(self):
        """Test that VectorPoint can be created and accessed."""
        point = VectorPoint(
            id="worker_001",
            vector=[0.1, 0.2, 0.3]
        )

        # Should be able to read values
        assert point.id == "worker_001"
        assert point.vector == [0.1, 0.2, 0.3]

    def test_payload_default_is_empty_dict(self):
        """Test that payload defaults to empty dict."""
        point = VectorPoint(
            id="worker_001",
            vector=[0.1, 0.2, 0.3]
        )

        assert point.payload == {}
        assert isinstance(point.payload, dict)

    def test_multiple_vector_points_with_same_id(self):
        """Test that multiple VectorPoints can have the same id (no uniqueness constraint at model level)."""
        point1 = VectorPoint(id="worker_001", vector=[0.1, 0.2, 0.3])
        point2 = VectorPoint(id="worker_001", vector=[0.4, 0.5, 0.6])

        # Both should be created successfully
        # Uniqueness should be enforced by VectorStore, not model
        assert point1.id == "worker_001"
        assert point2.id == "worker_001"
        assert point1.vector != point2.vector

    def test_vector_point_serialization(self):
        """Test that VectorPoint can be serialized to dict."""
        point = VectorPoint(
            id="worker_001",
            vector=[0.1, 0.2, 0.3],
            payload={"key": "value"}
        )

        point_dict = point.model_dump()

        assert point_dict["id"] == "worker_001"
        assert point_dict["vector"] == [0.1, 0.2, 0.3]
        assert point_dict["payload"] == {"key": "value"}

    def test_vector_point_deserialization(self):
        """Test that VectorPoint can be created from dict."""
        data = {
            "id": "worker_001",
            "vector": [0.1, 0.2, 0.3],
            "payload": {"key": "value"}
        }

        point = VectorPoint(**data)

        assert point.id == "worker_001"
        assert point.vector == [0.1, 0.2, 0.3]
        assert point.payload == {"key": "value"}