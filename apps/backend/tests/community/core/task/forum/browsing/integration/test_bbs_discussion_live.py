"""Live BBS integration: an ordinary discussion receives a useful Bot reply.

This is the first effect-bearing BBS tracer story.  It drives the real path:

    create ordinary Topic -> trigger real Browse run -> observe Bot reply

The test does not pass merely because the Browse run completed.  Its product
assertion is the reply persisted under the newly-created Topic by the addressed
Bot.  The reply must engage with the proposal by citing its unique marker and
must contribute a position, a reason, and a risk.

The Topic carries discussion semantics only in its title/body.  No platform
``topic_type`` hint is supplied.

The test is disabled by default.  Configure it only through environment
variables; credentials and session state must never be committed::

    TASK_OPENAPI_API_KEY=... \\
    TASK_OPENAPI_BASE_URL=http://localhost:8890 \\
    TASK_OPENAPI_BOT_ID=2026... \\
    BBS_LIVE_BACKEND_URL=http://localhost:8888 \\
    BBS_LIVE_USER_ID=146836 \\
      pytest -s tests/community/core/task/forum/browsing/integration/\\
        test_bbs_discussion_live.py

The configured Bot must already be allowed by the OpenAPI key.  The test joins
it to the BBS Browse Loop idempotently before triggering one immediate browse.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import unittest
import uuid
from typing import Any

import httpx

from agentclaw.community.core.task.task_runner.client.open_api_bot_adapter import (
    OpenApiBotAdapter,
)


API_KEY = os.getenv("TASK_OPENAPI_API_KEY", "")
API_KEY_PREFIX = os.getenv("TASK_OPENAPI_API_KEY_PREFIX", "")
BOT_API_BASE_URL = os.getenv("TASK_OPENAPI_BASE_URL", "")
COOKIE = os.getenv("TASK_OPENAPI_COOKIE", "")
REFERER = os.getenv("TASK_OPENAPI_REFERER", "")
BOT_ID = os.getenv("TASK_OPENAPI_BOT_ID", "")
BACKEND_BASE_URL = os.getenv("BBS_LIVE_BACKEND_URL", "")
USER_ID = os.getenv("BBS_LIVE_USER_ID", "")
TIMEOUT = float(os.getenv("BBS_LIVE_TIMEOUT", "600"))
POLL_INTERVAL = float(os.getenv("BBS_LIVE_POLL_INTERVAL", "5"))
JOIN_SETTLE_SECONDS = float(os.getenv("BBS_LIVE_JOIN_SETTLE_SECONDS", "30"))

_LIVE_ENABLED = bool(
    API_KEY and BOT_API_BASE_URL and BOT_ID and BACKEND_BASE_URL and USER_ID
)


class _LiveKey:
    """ApiKeyProvider backed exclusively by explicit live-test configuration."""

    api_key = API_KEY
    api_key_prefix = API_KEY_PREFIX
    base_url = BOT_API_BASE_URL
    cookie = COOKIE
    referer = REFERER


def _data(response: httpx.Response) -> dict[str, Any]:
    """Return one successful Avernet envelope's object data."""
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data")
    if not isinstance(data, dict):
        raise AssertionError(f"response has no object data: {payload}")
    return data


async def _wait_for_run(
    adapter: OpenApiBotAdapter,
    run_id: str,
    *,
    timeout: float,
    poll_interval: float,
) -> dict[str, Any]:
    """Wait for the Browse run so reply assertions inspect a settled attempt."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = await adapter.get_run(run_id)
        status = str(last.get("status") or "").upper()
        if status == "COMPLETED":
            return last
        if status == "FAILED":
            raise AssertionError(f"BBS Browse Bot run failed: {last}")
        await asyncio.sleep(poll_interval)
    raise AssertionError(
        f"BBS Browse Bot run {run_id} did not complete in {timeout}s; last={last}"
    )


@unittest.skipUnless(
    _LIVE_ENABLED,
    "设置 TASK_OPENAPI_API_KEY/BASE_URL/BOT_ID 与 "
    "BBS_LIVE_BACKEND_URL/BBS_LIVE_USER_ID 后启用 live 测试",
)
class TestBbsDiscussionLive(unittest.TestCase):
    def test_bot_adds_position_reason_and_risk_to_an_ordinary_discussion(self) -> None:
        marker = f"BBS-DISCUSSION-{uuid.uuid4().hex}"
        headers = {"x-user-id": USER_ID, "accept": "application/json"}
        adapter = OpenApiBotAdapter(_LiveKey())
        loop = asyncio.new_event_loop()
        topic_id = ""

        with httpx.Client(
            base_url=BACKEND_BASE_URL.rstrip("/"),
            headers=headers,
            timeout=60.0,
        ) as backend:
            try:
                topic_id = self._create_discussion_topic(backend, marker=marker)
                self._ensure_browse_subscription(backend)

                trigger = _data(
                    backend.post(
                        f"/api/v1/collaboration/tasks/bots/{BOT_ID}/bbs/"
                        "browse-loop/trigger-self"
                    )
                )
                run_id = str(trigger.get("run_id") or "")
                self.assertTrue(run_id, f"trigger returned no run_id: {trigger}")

                loop.run_until_complete(
                    _wait_for_run(
                        adapter,
                        run_id,
                        timeout=TIMEOUT,
                        poll_interval=POLL_INTERVAL,
                    )
                )

                replies = self._wait_for_bot_replies(backend, topic_id=topic_id)
                self.assertEqual(
                    len(replies),
                    1,
                    "one Browse run should contribute one idempotent discussion reply; "
                    f"topic_id={topic_id}, replies={replies}",
                )
                self._assert_meaningful_discussion_reply(
                    str(replies[0].get("body") or ""), marker=marker
                )
            finally:
                if topic_id:
                    self._close_topic(backend, topic_id=topic_id)
                loop.run_until_complete(adapter._aclose())
                loop.close()

    def _create_discussion_topic(
        self, backend: httpx.Client, *, marker: str
    ) -> str:
        response = backend.post(
            "/api/v1/collaboration/tasks/bbs/topics",
            json={
                "author_type": "HUMAN",
                "author_id": USER_ID,
                "author_display_name": "BBS live integration",
                "client_request_id": f"bbs-live-{uuid.uuid4().hex}",
                "title": "讨论：BBS 高风险建议是否必须附带回滚思路",
                "body": (
                    "想请社区一起判断下面的协作习惯是否值得采用。\n"
                    f"方案代号：{marker}\n"
                    "提议：Bot 在 BBS 提出高风险建议时，应同时说明一个主要风险和"
                    "一个可执行的回滚思路。\n"
                    f"请 {BOT_ID} 基于自己的判断参与讨论，不要只回复‘收到’。"
                    "请在同一条回复里使用‘立场：’、‘理由：’、‘风险：’三个标题，"
                    "每项都写出具体内容，并原样引用方案代号。"
                ),
            },
        )
        return str(_data(response)["topic_id"])

    def _ensure_browse_subscription(self, backend: httpx.Client) -> None:
        path = f"/api/v1/collaboration/tasks/bots/{BOT_ID}/bbs/browse-subscription"
        current = backend.get(path)
        if current.status_code == 200:
            return
        if current.status_code != 404:
            current.raise_for_status()

        response = backend.post(
            path,
            params={"owner_user_id": USER_ID},
            json={"note": "BBS discussion live integration"},
        )
        response.raise_for_status()
        # A first-time join installs the BBS skills asynchronously.  Do not
        # race that setup with the immediate Browse trigger.
        time.sleep(JOIN_SETTLE_SECONDS)

    def _wait_for_bot_replies(
        self, backend: httpx.Client, *, topic_id: str
    ) -> list[dict[str, Any]]:
        deadline = time.monotonic() + TIMEOUT
        last_items: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            data = _data(
                backend.get(
                    f"/api/v1/collaboration/tasks/bbs/topics/{topic_id}/posts",
                    params={"page": 1, "page_size": 100},
                )
            )
            last_items = data.get("items") or []
            bot_replies = [
                item
                for item in last_items
                if str(item.get("author_type") or "").upper() == "BOT"
                and str(item.get("author_id") or "") == BOT_ID
            ]
            if bot_replies:
                return bot_replies
            time.sleep(POLL_INTERVAL)
        self.fail(f"Bot did not reply to discussion {topic_id}; posts={last_items}")

    def _assert_meaningful_discussion_reply(self, body: str, *, marker: str) -> None:
        self.assertIn(marker, body, "reply did not engage with the tested proposal")
        for current, following in (("立场", "理由"), ("理由", "风险")):
            match = re.search(
                rf"{current}\s*[：:]\s*(.+?)(?={following}\s*[：:])",
                body,
                flags=re.DOTALL,
            )
            self.assertIsNotNone(match, f"reply has no structured {current}: {body}")
            self.assertGreaterEqual(
                len(match.group(1).strip()),
                4,
                f"reply's {current} is not substantive: {body}",
            )
        risk = re.search(r"风险\s*[：:]\s*(.+)$", body, flags=re.DOTALL)
        self.assertIsNotNone(risk, f"reply has no structured 风险: {body}")
        self.assertGreaterEqual(
            len(risk.group(1).strip()),
            4,
            f"reply's 风险 is not substantive: {body}",
        )

    def _close_topic(self, backend: httpx.Client, *, topic_id: str) -> None:
        response = backend.post(
            f"/api/v1/collaboration/tasks/bbs/topics/{topic_id}/close",
            json={"author_type": "HUMAN", "author_id": USER_ID},
        )
        if response.status_code >= 400:
            # Preserve the primary assertion failure while still making cleanup
            # trouble visible in live-test output.
            print(
                f"[cleanup] failed to close BBS topic {topic_id}: "
                f"{response.status_code} {response.text[:300]}"
            )


if __name__ == "__main__":
    unittest.main()
