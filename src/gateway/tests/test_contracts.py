"""Unit tests for the shared response-contract primitives."""

from __future__ import annotations

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
        code=CODE_OK, data=NameCheck(name="alice", exists=False), request_id="trace-1"
    )
    dumped = env.model_dump()
    assert set(dumped) == {"code", "message", "data", "request_id"}
    assert dumped["code"] == 200000
    assert dumped["message"] == "OK"
    assert dumped["data"] == {"name": "alice", "exists": False}
    assert dumped["request_id"] == "trace-1"


def test_envelope_defaults() -> None:
    env: Envelope[NameCheck] = Envelope(code=CODE_CREATED)
    assert env.message == "OK"
    assert env.data is None
    assert env.request_id == ""
    assert CODE_CREATED == 201000


def test_page_shape() -> None:
    page = Page[NameCheck](total=2, items=[NameCheck(name="a", exists=True)])
    dumped = page.model_dump()
    assert dumped["total"] == 2
    assert dumped["items"] == [{"name": "a", "exists": True}]


def test_page_defaults_to_empty_items() -> None:
    assert Page[NameCheck](total=0).items == []


def test_deleted_payload() -> None:
    assert Deleted().deleted is True
    assert Deleted(deleted=False).deleted is False


def test_page_params_defaults() -> None:
    params = PageParams()
    assert params.page == 1
    assert params.page_size == 20
