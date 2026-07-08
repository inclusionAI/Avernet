"""
Unit tests for bot_manage._models — focus on progress field coercion validators.
"""

import pytest
from pydantic import ValidationError

from secbaas.api.bot_manage._models import (
    BotStartProgressResponse,
    FetchStartProgressResult,
)


class TestProgressFieldCoercion:
    """Verify that both models coerce non-string progress values to string."""

    def test_bot_start_progress_response_progress_zero_int(self):
        """BotStartProgressResponse(progress=0) — progress should be '0' (str)."""
        model = BotStartProgressResponse(progress=0)
        assert model.progress == "0"
        assert isinstance(model.progress, str)

    def test_fetch_start_progress_result_progress_zero_int(self):
        """FetchStartProgressResult(progress=0) — progress should be '0' (str)."""
        model = FetchStartProgressResult(progress=0)
        assert model.progress == "0"
        assert isinstance(model.progress, str)

    def test_bot_start_progress_response_progress_percent_str(self):
        """BotStartProgressResponse(progress='25%') — progress should be '25%' (str passthrough)."""
        model = BotStartProgressResponse(progress="25%")
        assert model.progress == "25%"
        assert isinstance(model.progress, str)

    def test_fetch_start_progress_result_progress_ready_str(self):
        """FetchStartProgressResult(progress='ready') — progress should be 'ready' (str passthrough)."""
        model = FetchStartProgressResult(progress="ready")
        assert model.progress == "ready"
        assert isinstance(model.progress, str)

    def test_bot_start_progress_response_progress_hundred_int(self):
        """BotStartProgressResponse(progress=100) — progress should be '100' (str)."""
        model = BotStartProgressResponse(progress=100)
        assert model.progress == "100"
        assert isinstance(model.progress, str)

    def test_bot_start_progress_response_progress_none(self):
        """BotStartProgressResponse(progress=None) — must raise ValidationError.

        None values from the mng daemon should be rejected explicitly rather
        than silently converted to the literal string "None", which would mask
        upstream errors.
        """
        with pytest.raises(ValidationError):
            BotStartProgressResponse(progress=None)
