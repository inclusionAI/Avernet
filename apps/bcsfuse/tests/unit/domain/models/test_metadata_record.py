"""Tests for MetadataRecord domain model."""

import pytest
from pydantic import ValidationError

from src.domain.models.metadata_record import MetadataRecord


class TestMetadataRecord:
    """Test MetadataRecord model."""

    def test_create_metadata_record_with_required_fields(self):
        """Test creating MetadataRecord with only required fields."""
        record = MetadataRecord(
            profile_key="staff_123:default",
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=["backend", "frontend"],
            active_skill_names=["python", "java"],
            suitable_roles=["developer", "architect"],
            source_root="/data/workers"
        )

        assert record.profile_key == "staff_123:default"
        assert record.staff_id == "123"
        assert record.profile_id == "default"
        assert record.profile_type == "default"
        assert record.domains == ["backend", "frontend"]
        assert record.active_skill_names == ["python", "java"]
        assert record.suitable_roles == ["developer", "architect"]
        assert record.source_root == "/data/workers"
        assert record.vector_id is None
        assert record.payload == {}

    def test_create_metadata_record_with_all_fields(self):
        """Test creating MetadataRecord with all fields."""
        record = MetadataRecord(
            profile_key="staff_123:default",
            vector_id=42,
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=["backend", "frontend"],
            active_skill_names=["python", "java"],
            suitable_roles=["developer", "architect"],
            source_root="/data/workers",
            payload={"extra": "data"}
        )

        assert record.profile_key == "staff_123:default"
        assert record.vector_id == 42
        assert record.staff_id == "123"
        assert record.profile_id == "default"
        assert record.profile_type == "default"
        assert record.domains == ["backend", "frontend"]
        assert record.active_skill_names == ["python", "java"]
        assert record.suitable_roles == ["developer", "architect"]
        assert record.source_root == "/data/workers"
        assert record.payload == {"extra": "data"}

    def test_missing_profile_key_raises_error(self):
        """Test that missing profile_key raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            MetadataRecord(
                staff_id="123",
                profile_id="default",
                profile_type="default",
                domains=[],
                active_skill_names=[],
                suitable_roles=[],
                source_root="/data"
            )

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("profile_key",) and e["type"] == "missing" for e in errors)

    def test_missing_staff_id_raises_error(self):
        """Test that missing staff_id raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            MetadataRecord(
                profile_key="staff_123:default",
                profile_id="default",
                profile_type="default",
                domains=[],
                active_skill_names=[],
                suitable_roles=[],
                source_root="/data"
            )

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("staff_id",) and e["type"] == "missing" for e in errors)

    def test_missing_profile_id_raises_error(self):
        """Test that missing profile_id raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            MetadataRecord(
                profile_key="staff_123:default",
                staff_id="123",
                profile_type="default",
                domains=[],
                active_skill_names=[],
                suitable_roles=[],
                source_root="/data"
            )

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("profile_id",) and e["type"] == "missing" for e in errors)

    def test_missing_profile_type_raises_error(self):
        """Test that missing profile_type raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            MetadataRecord(
                profile_key="staff_123:default",
                staff_id="123",
                profile_id="default",
                domains=[],
                active_skill_names=[],
                suitable_roles=[],
                source_root="/data"
            )

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("profile_type",) and e["type"] == "missing" for e in errors)

    def test_missing_source_root_raises_error(self):
        """Test that missing source_root raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            MetadataRecord(
                profile_key="staff_123:default",
                staff_id="123",
                profile_id="default",
                profile_type="default",
                domains=[],
                active_skill_names=[],
                suitable_roles=[]
            )

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("source_root",) and e["type"] == "missing" for e in errors)

    def test_empty_domains_allowed(self):
        """Test that empty domains list is allowed."""
        record = MetadataRecord(
            profile_key="staff_123:default",
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=[],
            active_skill_names=["python"],
            suitable_roles=["developer"],
            source_root="/data/workers"
        )

        assert record.domains == []

    def test_empty_active_skill_names_allowed(self):
        """Test that empty active_skill_names list is allowed."""
        record = MetadataRecord(
            profile_key="staff_123:default",
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=["backend"],
            active_skill_names=[],
            suitable_roles=["developer"],
            source_root="/data/workers"
        )

        assert record.active_skill_names == []

    def test_empty_suitable_roles_allowed(self):
        """Test that empty suitable_roles list is allowed."""
        record = MetadataRecord(
            profile_key="staff_123:default",
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=["backend"],
            active_skill_names=["python"],
            suitable_roles=[],
            source_root="/data/workers"
        )

        assert record.suitable_roles == []

    def test_vector_id_can_be_none(self):
        """Test that vector_id can be None (not yet indexed)."""
        record = MetadataRecord(
            profile_key="staff_123:default",
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=["backend"],
            active_skill_names=["python"],
            suitable_roles=["developer"],
            source_root="/data/workers"
        )

        assert record.vector_id is None

    def test_vector_id_can_be_zero(self):
        """Test that vector_id can be 0 (valid Faiss index)."""
        record = MetadataRecord(
            profile_key="staff_123:default",
            vector_id=0,
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=["backend"],
            active_skill_names=["python"],
            suitable_roles=["developer"],
            source_root="/data/workers"
        )

        assert record.vector_id == 0

    def test_vector_id_can_be_large(self):
        """Test that vector_id can be a large number."""
        record = MetadataRecord(
            profile_key="staff_123:default",
            vector_id=999999,
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=["backend"],
            active_skill_names=["python"],
            suitable_roles=["developer"],
            source_root="/data/workers"
        )

        assert record.vector_id == 999999

    def test_domains_list_behavior(self):
        """Test domains as list field."""
        record = MetadataRecord(
            profile_key="staff_123:default",
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=["backend", "frontend", "devops"],
            active_skill_names=["python"],
            suitable_roles=["developer"],
            source_root="/data/workers"
        )

        assert len(record.domains) == 3
        assert "backend" in record.domains
        assert "frontend" in record.domains
        assert "devops" in record.domains

    def test_active_skill_names_list_behavior(self):
        """Test active_skill_names as list field."""
        record = MetadataRecord(
            profile_key="staff_123:default",
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=["backend"],
            active_skill_names=["python", "java", "go", "rust"],
            suitable_roles=["developer"],
            source_root="/data/workers"
        )

        assert len(record.active_skill_names) == 4
        assert "python" in record.active_skill_names
        assert "rust" in record.active_skill_names

    def test_suitable_roles_list_behavior(self):
        """Test suitable_roles as list field."""
        record = MetadataRecord(
            profile_key="staff_123:default",
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=["backend"],
            active_skill_names=["python"],
            suitable_roles=["developer", "architect", "tech_lead"],
            source_root="/data/workers"
        )

        assert len(record.suitable_roles) == 3
        assert "developer" in record.suitable_roles
        assert "tech_lead" in record.suitable_roles

    def test_payload_can_be_any_dict(self):
        """Test that payload can contain various types."""
        record = MetadataRecord(
            profile_key="staff_123:default",
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=["backend"],
            active_skill_names=["python"],
            suitable_roles=["developer"],
            source_root="/data/workers",
            payload={
                "string": "value",
                "int": 123,
                "float": 0.5,
                "bool": True,
                "list": ["a", "b", "c"],
                "nested": {"key": "value"}
            }
        )

        assert record.payload["string"] == "value"
        assert record.payload["int"] == 123
        assert record.payload["float"] == 0.5
        assert record.payload["bool"] is True
        assert record.payload["list"] == ["a", "b", "c"]
        assert record.payload["nested"] == {"key": "value"}

    def test_extra_fields_forbidden(self):
        """Test that extra fields are forbidden."""
        with pytest.raises(ValidationError) as exc_info:
            MetadataRecord(
                profile_key="staff_123:default",
                staff_id="123",
                profile_id="default",
                profile_type="default",
                domains=["backend"],
                active_skill_names=["python"],
                suitable_roles=["developer"],
                source_root="/data/workers",
                extra_field="not_allowed"
            )

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["type"] == "extra_forbidden"

    def test_metadata_record_serialization(self):
        """Test that MetadataRecord can be serialized to dict."""
        record = MetadataRecord(
            profile_key="staff_123:default",
            vector_id=42,
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=["backend"],
            active_skill_names=["python"],
            suitable_roles=["developer"],
            source_root="/data/workers",
            payload={"key": "value"}
        )

        record_dict = record.model_dump()

        assert record_dict["profile_key"] == "staff_123:default"
        assert record_dict["vector_id"] == 42
        assert record_dict["staff_id"] == "123"
        assert record_dict["profile_id"] == "default"
        assert record_dict["profile_type"] == "default"
        assert record_dict["domains"] == ["backend"]
        assert record_dict["active_skill_names"] == ["python"]
        assert record_dict["suitable_roles"] == ["developer"]
        assert record_dict["source_root"] == "/data/workers"
        assert record_dict["payload"] == {"key": "value"}

    def test_metadata_record_deserialization(self):
        """Test that MetadataRecord can be created from dict."""
        data = {
            "profile_key": "staff_123:default",
            "vector_id": 42,
            "staff_id": "123",
            "profile_id": "default",
            "profile_type": "default",
            "domains": ["backend"],
            "active_skill_names": ["python"],
            "suitable_roles": ["developer"],
            "source_root": "/data/workers",
            "payload": {"key": "value"}
        }

        record = MetadataRecord(**data)

        assert record.profile_key == "staff_123:default"
        assert record.vector_id == 42
        assert record.staff_id == "123"
        assert record.profile_id == "default"
        assert record.profile_type == "default"
        assert record.domains == ["backend"]
        assert record.active_skill_names == ["python"]
        assert record.suitable_roles == ["developer"]
        assert record.source_root == "/data/workers"
        assert record.payload == {"key": "value"}

    def test_profile_key_format_variations(self):
        """Test various valid profile_key formats."""
        # Format 1: staff_{staff_id}:{profile_id}
        record1 = MetadataRecord(
            profile_key="staff_123:default",
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=[],
            active_skill_names=[],
            suitable_roles=[],
            source_root="/data"
        )
        assert record1.profile_key == "staff_123:default"

        # Format 2: bot_{staff_id}:{bot_name}
        record2 = MetadataRecord(
            profile_key="bot_456:assistant",
            staff_id="456",
            profile_id="assistant",
            profile_type="bot",
            domains=[],
            active_skill_names=[],
            suitable_roles=[],
            source_root="/data"
        )
        assert record2.profile_key == "bot_456:assistant"

        # Format 3: custom format
        record3 = MetadataRecord(
            profile_key="custom_key_789",
            staff_id="789",
            profile_id="custom",
            profile_type="default",
            domains=[],
            active_skill_names=[],
            suitable_roles=[],
            source_root="/data"
        )
        assert record3.profile_key == "custom_key_789"

    def test_profile_type_variations(self):
        """Test various profile_type values."""
        # Default type
        record1 = MetadataRecord(
            profile_key="staff_123:default",
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=[],
            active_skill_names=[],
            suitable_roles=[],
            source_root="/data"
        )
        assert record1.profile_type == "default"

        # Bot type
        record2 = MetadataRecord(
            profile_key="staff_456:bot",
            staff_id="456",
            profile_id="bot",
            profile_type="bot",
            domains=[],
            active_skill_names=[],
            suitable_roles=[],
            source_root="/data"
        )
        assert record2.profile_type == "bot"

        # Custom type
        record3 = MetadataRecord(
            profile_key="staff_789:custom",
            staff_id="789",
            profile_id="custom",
            profile_type="custom_type",
            domains=[],
            active_skill_names=[],
            suitable_roles=[],
            source_root="/data"
        )
        assert record3.profile_type == "custom_type"

    def test_source_root_variations(self):
        """Test various source_root values."""
        # Absolute path
        record1 = MetadataRecord(
            profile_key="staff_123:default",
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=[],
            active_skill_names=[],
            suitable_roles=[],
            source_root="/data/workers/profiles"
        )
        assert record1.source_root == "/data/workers/profiles"

        # Relative path
        record2 = MetadataRecord(
            profile_key="staff_456:default",
            staff_id="456",
            profile_id="default",
            profile_type="default",
            domains=[],
            active_skill_names=[],
            suitable_roles=[],
            source_root="./data/workers"
        )
        assert record2.source_root == "./data/workers"

        # URL
        record3 = MetadataRecord(
            profile_key="staff_789:default",
            staff_id="789",
            profile_id="default",
            profile_type="default",
            domains=[],
            active_skill_names=[],
            suitable_roles=[],
            source_root="https://api.example.com/profiles"
        )
        assert record3.source_root == "https://api.example.com/profiles"

    def test_payload_default_is_empty_dict(self):
        """Test that payload defaults to empty dict."""
        record = MetadataRecord(
            profile_key="staff_123:default",
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=[],
            active_skill_names=[],
            suitable_roles=[],
            source_root="/data/workers"
        )

        assert record.payload == {}
        assert isinstance(record.payload, dict)

    def test_vector_id_default_is_none(self):
        """Test that vector_id defaults to None."""
        record = MetadataRecord(
            profile_key="staff_123:default",
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=[],
            active_skill_names=[],
            suitable_roles=[],
            source_root="/data/workers"
        )

        assert record.vector_id is None