from __future__ import annotations

import asyncio

import pytest

from agentclaw.community.core.task.services.bot_grant_reconciliation_service import (
    BotGrantReconciliationService,
)


class _Bots:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.list_calls: list[tuple[str, int, int]] = []
        self.get_calls: list[tuple[str, str]] = []

    def list_by_owner(self, owner_id: str, page: int = 1, page_size: int = 20):
        self.list_calls.append((owner_id, page, page_size))
        start = (page - 1) * page_size
        return len(self.rows), self.rows[start : start + page_size]

    def get_by_id_and_owner(self, bot_id: str, owner_id: str):
        self.get_calls.append((bot_id, owner_id))
        return next(
            (
                row
                for row in self.rows
                if row["bot_id"] == bot_id and row["owner_id"] == owner_id
            ),
            None,
        )


class _Grants:
    def __init__(self, *, fail: set[str] | None = None, block: set[str] | None = None) -> None:
        self.calls: list[dict[str, str]] = []
        self.fail = fail or set()
        self.block = block or set()

    async def grant(self, **kwargs):
        self.calls.append(kwargs)
        bot_id = kwargs["bcs_bot_id"]
        if bot_id in self.fail:
            raise RuntimeError("grant failed")
        if bot_id in self.block:
            await asyncio.sleep(60)


@pytest.mark.asyncio
async def test_reconcile_all_owned_bots_sequentially_with_bcn_identity():
    bots = _Bots([
        {"bot_id": "personal", "owner_id": "u-1", "bot_type": "personal"},
        {
            "bot_id": "service",
            "owner_id": "u-1",
            "bot_type": "service",
            "ext": {"bcn_provider_bot_ref": "service:provider-entity"},
        },
    ])
    grants = _Grants()
    service = BotGrantReconciliationService(bots=bots, grants=grants, page_size=1)

    await service.reconcile(owner_id="u-1", cookie="cookie", referer="referer")

    assert bots.list_calls == [("u-1", 1, 1), ("u-1", 2, 1)]
    assert [call["bcs_bot_id"] for call in grants.calls] == [
        "personal:u-1",
        "service:provider-entity",
    ]
    assert all(call["cookie"] == "cookie" for call in grants.calls)
    assert all(call["referer"] == "referer" for call in grants.calls)
    assert all(call["operator"] == "u-1" for call in grants.calls)


@pytest.mark.asyncio
async def test_reconcile_one_bot_is_scoped_to_its_current_owner():
    bots = _Bots([
        {"bot_id": "owned", "owner_id": "u-1", "bot_type": "personal"},
        {"bot_id": "other", "owner_id": "u-2", "bot_type": "personal"},
    ])
    grants = _Grants()
    service = BotGrantReconciliationService(bots=bots, grants=grants)

    await service.reconcile(
        owner_id="u-1",
        cookie="cookie",
        referer="referer",
        bot_id="other",
    )

    assert bots.get_calls == [("other", "u-1")]
    assert grants.calls == []


@pytest.mark.asyncio
async def test_one_failed_grant_does_not_stop_later_bots():
    bots = _Bots([
        {"bot_id": "first", "owner_id": "u-1", "bot_type": "personal"},
        {"bot_id": "second", "owner_id": "u-1", "bot_type": "personal"},
    ])
    grants = _Grants(fail={"first:u-1"})
    service = BotGrantReconciliationService(bots=bots, grants=grants)

    await service.reconcile(owner_id="u-1", cookie="cookie", referer="referer")

    assert [call["bcs_bot_id"] for call in grants.calls] == ["first:u-1", "second:u-1"]


@pytest.mark.asyncio
async def test_one_timed_out_grant_does_not_stop_later_bots():
    bots = _Bots([
        {"bot_id": "slow", "owner_id": "u-1", "bot_type": "personal"},
        {"bot_id": "next", "owner_id": "u-1", "bot_type": "personal"},
    ])
    grants = _Grants(block={"slow:u-1"})
    service = BotGrantReconciliationService(
        bots=bots,
        grants=grants,
        grant_timeout_seconds=0.01,
    )

    await service.reconcile(owner_id="u-1", cookie="cookie", referer="referer")

    assert [call["bcs_bot_id"] for call in grants.calls] == ["slow:u-1", "next:u-1"]
