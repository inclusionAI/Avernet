"""Unit tests for the shared response-contract primitives."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gateway.community.adapters.web.contracts import (
    CODE_CREATED,
    CODE_OK,
    Deleted,
    Envelope,
    NameCheck,
    Page,
    PageParams,
)


def test_envelope_shape() -> None:
    env = Envelope[NameCheck](
        code=CODE_OK,
        message="OK",
        data=NameCheck(name="alice", exists=False),
        request_id="trace-1",
    )
    dumped = env.model_dump()
    assert set(dumped) == {"code", "message", "data", "request_id"}
    assert dumped["code"] == 200000
    assert dumped["message"] == "OK"
    assert dumped["data"] == {"name": "alice", "exists": False}
    assert dumped["request_id"] == "trace-1"


def test_envelope_all_fields_required() -> None:
    # Every response advertises {code, message, data, request_id}; none omittable.
    with pytest.raises(ValidationError):
        Envelope[NameCheck](code=CODE_CREATED)  # type: ignore[call-arg]


def test_envelope_data_is_nullable_but_present() -> None:
    env = Envelope[NameCheck](
        code=CODE_CREATED, message="Created", data=None, request_id="t"
    )
    assert env.data is None
    assert "data" in env.model_dump()  # present, just null
    assert CODE_CREATED == 201000


def test_envelope_schema_marks_all_fields_required() -> None:
    schema = Envelope[NameCheck].model_json_schema()
    assert set(schema["required"]) == {"code", "message", "data", "request_id"}


def test_page_shape() -> None:
    page = Page[NameCheck](total=2, items=[NameCheck(name="a", exists=True)])
    dumped = page.model_dump()
    assert dumped["total"] == 2
    assert dumped["items"] == [{"name": "a", "exists": True}]


def test_page_items_required_but_may_be_empty() -> None:
    with pytest.raises(ValidationError):
        Page[NameCheck](total=0)  # type: ignore[call-arg]
    assert Page[NameCheck](total=0, items=[]).items == []


def test_deleted_payload() -> None:
    assert Deleted().deleted is True
    assert Deleted(deleted=False).deleted is False


def test_page_params_defaults() -> None:
    params = PageParams()
    assert params.page == 1
    assert params.page_size == 20
