"""Unit tests for BotStartProgressResponse and FetchStartProgressResult models."""

import pytest
from pydantic import ValidationError


class TestBotStartProgressResponse:
    """Tests for BotStartProgressResponse Pydantic model."""

    def test_construction_progress_only(self):
        """WHEN constructed with progress only, THEN model validates."""
        from secbaas.community.api.bot_manage import BotStartProgressResponse

        model = BotStartProgressResponse(progress="in_progress")
        assert model.progress == "in_progress"
        assert model.model_dump().get("error_message") is None

    def test_construction_with_error_message(self):
        """WHEN constructed with error_message, THEN all fields populate."""
        from secbaas.community.api.bot_manage import BotStartProgressResponse

        model = BotStartProgressResponse(
            progress="failed",
            error_message="connection timeout",
        )
        assert model.progress == "failed"
        assert model.error_message == "connection timeout"

    def test_construction_with_extra_fields(self):
        """WHEN constructed with extra fields, THEN they are preserved via extra='allow'."""
        from secbaas.community.api.bot_manage import BotStartProgressResponse

        model = BotStartProgressResponse(
            progress="completed",
            current_phase="ready",
            overall_status="completed",
        )
        assert model.progress == "completed"
        assert model.current_phase == "ready"  # type: ignore[attr-defined]
        assert model.overall_status == "completed"  # type: ignore[attr-defined]

    def test_model_dump_includes_extra_fields(self):
        """WHEN model_dump() is called, THEN extra fields are included."""
        from secbaas.community.api.bot_manage import BotStartProgressResponse

        model = BotStartProgressResponse(
            progress="in_progress",
            current_phase="pulling_image",
            custom_field=42,
        )
        dumped = model.model_dump()
        assert dumped["progress"] == "in_progress"
        assert dumped.get("error_message") is None
        assert dumped["current_phase"] == "pulling_image"
        assert dumped["custom_field"] == 42

    def test_backward_compatible_current_phase_overall_status(self):
        """WHEN old-style fields (current_phase + overall_status) are passed, THEN they are
        preserved as extra fields."""
        from secbaas.community.api.bot_manage import BotStartProgressResponse

        model = BotStartProgressResponse(
            progress="in_progress",
            current_phase="health_check",
            overall_status="in_progress",
        )
        assert model.current_phase == "health_check"  # type: ignore[attr-defined]
        assert model.overall_status == "in_progress"  # type: ignore[attr-defined]

    def test_progress_is_required(self):
        """WHEN progress is missing, THEN ValidationError raised."""
        from secbaas.community.api.bot_manage import BotStartProgressResponse

        with pytest.raises(ValidationError, match="progress"):
            BotStartProgressResponse()
        with pytest.raises(ValidationError, match="progress"):
            BotStartProgressResponse(current_phase="ready")

    def test_error_message_defaults_to_none(self):
        """WHEN error_message is not provided, THEN default is None."""
        from secbaas.community.api.bot_manage import BotStartProgressResponse

        model = BotStartProgressResponse(progress="in_progress")
        assert model.model_dump().get("error_message") is None


class TestFetchStartProgressResult:
    """Tests for FetchStartProgressResult Pydantic model."""

    def test_construction_progress_only(self):
        """WHEN constructed with progress only, THEN model validates."""
        from secbaas.community.api.bot_manage import FetchStartProgressResult

        model = FetchStartProgressResult(progress="in_progress")
        assert model.progress == "in_progress"
        assert model.model_dump().get("error_message") is None

    def test_construction_with_error_message(self):
        """WHEN constructed with error_message, THEN all fields populate."""
        from secbaas.community.api.bot_manage import FetchStartProgressResult

        model = FetchStartProgressResult(
            progress="failed",
            error_message="timeout",
        )
        assert model.progress == "failed"
        assert model.error_message == "timeout"

    def test_construction_with_extra_fields(self):
        """WHEN constructed with extra fields, THEN they are preserved via extra='allow'."""
        from secbaas.community.api.bot_manage import FetchStartProgressResult

        model = FetchStartProgressResult(
            progress="completed",
            current_phase="ready",
            overall_status="completed",
        )
        assert model.progress == "completed"
        assert model.current_phase == "ready"  # type: ignore[attr-defined]
        assert model.overall_status == "completed"  # type: ignore[attr-defined]

    def test_model_dump_includes_extra_fields(self):
        """WHEN model_dump() is called, THEN extra fields are included."""
        from secbaas.community.api.bot_manage import FetchStartProgressResult

        model = FetchStartProgressResult(
            progress="in_progress",
            current_phase="creating_container",
            overall_status="in_progress",
        )
        dumped = model.model_dump()
        assert dumped["progress"] == "in_progress"
        assert dumped["current_phase"] == "creating_container"
        assert dumped["overall_status"] == "in_progress"

    def test_progress_is_required(self):
        """WHEN progress is missing, THEN ValidationError raised."""
        from secbaas.community.api.bot_manage import FetchStartProgressResult

        with pytest.raises(ValidationError):
            FetchStartProgressResult()

    def test_structural_independence(self):
        """WHEN compared, THEN FetchStartProgressResult != BotStartProgressResponse."""
        from secbaas.community.api.bot_manage import (
            BotStartProgressResponse,
            FetchStartProgressResult,
        )

        pr = FetchStartProgressResult(progress="completed")
        br = BotStartProgressResponse(progress="completed")
        # They are different types even with same field values
        assert type(pr) is not type(br)
        assert isinstance(pr, FetchStartProgressResult)
        assert not isinstance(pr, BotStartProgressResponse)
