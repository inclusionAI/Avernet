"""Tests for response-boundary session ID extraction."""

import pytest

from secbaas.community.adapters.web.session_id import extract_response_session_id


@pytest.mark.parametrize(
    ("metadata", "result_extra", "expected"),
    [
        (
            {"session_id": "from-metadata"},
            {"session_id": "from-extra"},
            "from-metadata",
        ),
        ({"session_id": None}, {"session_id": "from-extra"}, "from-extra"),
        ({"session_id": 123}, {"session_id": "from-extra"}, "from-extra"),
        ({"other": "value"}, {"session_id": "from-extra"}, "from-extra"),
        (None, {"session_id": "from-extra"}, "from-extra"),
        ({"session_id": None}, {"session_id": 123}, ""),
        (None, None, ""),
        ({"session_id": ""}, {"session_id": "from-extra"}, ""),
    ],
)
def test_extract_response_session_id(metadata, result_extra, expected):
    assert extract_response_session_id(metadata, result_extra) == expected
