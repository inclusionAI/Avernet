"""Openapi surface for the BBS Browse-Loop toggle — POST join / DELETE cancel.

These are the public /openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/browse-subscription writes.
The surface is B-scheme only: the periodic forum tour is always driven by the
OpenClaw cron, so the join exposes no trigger-mode selector and reads its owner
off the required owner_user_id query parameter rather than the request body. The
handlers are invoked directly with stubs for the forum service, the OpenClaw
cron manager and the framework scheduler, so the assertions are about wiring
(openclaw cron always installed on join, idempotent 201/200, mode swap and
delete) rather than the injector.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import Request, Response

from agentclaw.community.adapters.http.openapi_v1.task.bbs.browse_router import (
    delete_bbs_browse_subscription,
    upsert_bbs_browse_subscription,
)
from agentclaw.community.adapters.http.openapi_v1.task.bbs.router import (
    list_bbs_browse_subscriptions,
)
from agentclaw.community.adapters.http.openapi_v1.task.bbs.schemas import (
    BrowsSubscriptionJoinRequest,
    SubscriptionItem,
)
from agentclaw.community.adapters.http.openapi_v1.contracts import PageParams
from agentclaw.community.core.task.forum.browsing.subscription_provisioner import (
    BrowseSubscriptionProvisionResult,
)
from agentclaw.community.core.task.forum.models import (
    BrowseSubscriptionPage,
    BrowseSubscriptionRecord,
)


def _request(method: str, path: str) -> Request:
    request = Request(scope={"type": "http", "method": method, "path": path})
    request.state.trace_id = "trace-browse-openapi"
    return request


def _record(*, bot_id: str = "bot-a", mode: str = "openclaw", note=None):
    now = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
    return BrowseSubscriptionRecord(
        bot_id=bot_id,
        owner_user_id="111111",
        mode=mode,
        note=note,
        created_at=now,
        updated_at=now,
    )


class _FakeProvisioner:
    """Records delegation to the asynchronous provisioning service."""

    def __init__(self, result: BrowseSubscriptionProvisionResult) -> None:
        self._result = result
        self.joins: list[dict[str, str | None]] = []
        self.cancels: list[str] = []

    def join_or_refresh(self, *, bot_id: str, owner_user_id: str, note: str | None):
        self.joins.append(
            {"bot_id": bot_id, "owner_user_id": owner_user_id, "note": note}
        )
        return self._result

    async def cancel_pending(self, *, bot_id: str) -> None:
        self.cancels.append(bot_id)


class _FakeCronManager:
    """Records ensure_cron/remove_cron calls."""

    def __init__(self) -> None:
        self.ensures: list[dict[str, str]] = []
        self.removes: list[dict[str, str]] = []

    async def ensure_cron(self, *, bot_id, owner_user_id):
        self.ensures.append({"bot_id": bot_id, "owner_user_id": owner_user_id})
        return {"success": True}

    async def remove_cron(self, *, bot_id, owner_user_id):
        self.removes.append({"bot_id": bot_id, "owner_user_id": owner_user_id})
        return None


class _FakeScheduler:
    """Records register_bot/unregister_bot calls (legacy framework path)."""

    def __init__(self) -> None:
        self.registered: list[str] = []
        self.unregistered: list[str] = []

    def register_bot(self, bot_id):
        self.registered.append(bot_id)

    def unregister_bot(self, bot_id):
        self.unregistered.append(bot_id)


# ---------------------------------------------------------------------------
# POST browse-subscription — join (openclaw / B-scheme) only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_new_subscription_is_accepted_async():
    provisioner = _FakeProvisioner(
        BrowseSubscriptionProvisionResult(
            subscription=_record(mode="openclaw", note="self cron"),
            created=True,
            persisted=False,
        )
    )
    response = Response()
    payload = await upsert_bbs_browse_subscription(
        body=BrowsSubscriptionJoinRequest(note="self cron"),
        bot_id="bot-a",
        request=_request("POST", "/openapi/v1/bots/bot-a/bbs/browse-subscription"),
        response=response,
        owner_user_id="111111",
        provisioner=provisioner,
    )
    assert response.status_code == 202
    assert payload.code == 202000
    assert isinstance(payload.data, SubscriptionItem)
    assert payload.data.mode == "openclaw" and payload.data.note == "self cron"
    assert provisioner.joins == [
        {"bot_id": "bot-a", "owner_user_id": "111111", "note": "self cron"}
    ]


@pytest.mark.asyncio
async def test_upsert_existing_openclaw_subscription_returns_persisted_record():
    provisioner = _FakeProvisioner(
        BrowseSubscriptionProvisionResult(
            subscription=_record(mode="openclaw", note=None),
            created=False,
            persisted=True,
        )
    )
    response = Response()
    payload = await upsert_bbs_browse_subscription(
        body=None,
        bot_id="bot-a",
        request=_request("POST", "/openapi/v1/bots/bot-a/bbs/browse-subscription"),
        response=response,
        owner_user_id="111111",
        provisioner=provisioner,
    )
    assert response.status_code == 200
    assert payload.code == 200000
    assert provisioner.joins == [
        {"bot_id": "bot-a", "owner_user_id": "111111", "note": None}
    ]


# ---------------------------------------------------------------------------
# DELETE browse-subscription — cancel (idempotent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_openclaw_subscription_removes_cron():
    class Service:
        def get_subscription(self, **kwargs):
            return _record(mode="openclaw")

        def delete_subscription(self, **kwargs):
            assert kwargs == {"bot_id": "bot-a"}
            return True

    cron_manager = _FakeCronManager()
    digest_cron_manager = _FakeCronManager()
    scheduler = _FakeScheduler()
    payload = await delete_bbs_browse_subscription(
        bot_id="bot-a",
        request=_request("DELETE", "/openapi/v1/bots/bot-a/bbs/browse-subscription"),
        service=Service(),
        cron_manager=cron_manager,
        digest_cron_manager=digest_cron_manager,
        scheduler=scheduler,
        provisioner=_FakeProvisioner(
            BrowseSubscriptionProvisionResult(
                subscription=_record(mode="openclaw"),
                created=False,
                persisted=False,
            )
        ),
    )
    assert payload.data is not None and payload.data.deleted is True
    assert cron_manager.removes == [{"bot_id": "bot-a", "owner_user_id": "111111"}]
    assert digest_cron_manager.removes == [
        {"bot_id": "bot-a", "owner_user_id": "111111"}
    ]
    assert scheduler.unregistered == []


@pytest.mark.asyncio
async def test_delete_legacy_framework_subscription_unregisters_scheduler():
    class Service:
        def get_subscription(self, **kwargs):
            return _record(mode="framework")

        def delete_subscription(self, **kwargs):
            return True

    cron_manager = _FakeCronManager()
    digest_cron_manager = _FakeCronManager()
    scheduler = _FakeScheduler()
    payload = await delete_bbs_browse_subscription(
        bot_id="bot-a",
        request=_request("DELETE", "/openapi/v1/bots/bot-a/bbs/browse-subscription"),
        service=Service(),
        cron_manager=cron_manager,
        digest_cron_manager=digest_cron_manager,
        scheduler=scheduler,
        provisioner=_FakeProvisioner(
            BrowseSubscriptionProvisionResult(
                subscription=_record(mode="framework"),
                created=False,
                persisted=False,
            )
        ),
    )
    assert payload.data is not None and payload.data.deleted is True
    assert scheduler.unregistered == ["bot-a"]
    assert cron_manager.removes == []


@pytest.mark.asyncio
async def test_delete_missing_subscription_is_idempotent():
    class Service:
        def get_subscription(self, **kwargs):
            return None

        def delete_subscription(self, **kwargs):
            return False

    cron_manager = _FakeCronManager()
    digest_cron_manager = _FakeCronManager()
    scheduler = _FakeScheduler()
    payload = await delete_bbs_browse_subscription(
        bot_id="bot-a",
        request=_request("DELETE", "/openapi/v1/bots/bot-a/bbs/browse-subscription"),
        service=Service(),
        cron_manager=cron_manager,
        digest_cron_manager=digest_cron_manager,
        scheduler=scheduler,
        provisioner=_FakeProvisioner(
            BrowseSubscriptionProvisionResult(
                subscription=_record(mode="openclaw"),
                created=False,
                persisted=False,
            )
        ),
    )
    assert payload.data is not None and payload.data.deleted is False
    assert cron_manager.removes == [] and scheduler.unregistered == []


# ---------------------------------------------------------------------------
# GET /openapi/v1/collaboration/tasks/bbs/browse-subscriptions — list by owner (per-user matrix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_subscriptions_scopes_to_actor_owner():
    seen = {}

    class Service:
        def list_subscriptions(self, **kwargs):
            seen.update(kwargs)
            return BrowseSubscriptionPage(
                total=2,
                items=(
                    _record(bot_id="bot-a", mode="openclaw", note="n1"),
                    _record(bot_id="bot-b", mode="openclaw", note=None),
                ),
            )

    payload = await list_bbs_browse_subscriptions(
        request=_request(
            "GET", "/openapi/v1/collaboration/tasks/bbs/browse-subscriptions"
        ),
        owner_user_id="111111",
        page_params=PageParams(page=1, page_size=20),
        service=Service(),
    )
    assert seen == {
        "page": 1,
        "page_size": 20,
        "owner_user_id": "111111",
    }
    assert payload.data.total == 2
    assert [item.bot_id for item in payload.data.items] == ["bot-a", "bot-b"]
    assert all(isinstance(item, SubscriptionItem) for item in payload.data.items)


@pytest.mark.asyncio
async def test_list_subscriptions_empty_when_owner_has_none():
    class Service:
        def list_subscriptions(self, **kwargs):
            assert kwargs["owner_user_id"] == "999999"
            return BrowseSubscriptionPage(total=0, items=())

    payload = await list_bbs_browse_subscriptions(
        request=_request(
            "GET", "/openapi/v1/collaboration/tasks/bbs/browse-subscriptions"
        ),
        owner_user_id="999999",
        page_params=PageParams(page=1, page_size=20),
        service=Service(),
    )
    assert payload.data.total == 0 and payload.data.items == []
