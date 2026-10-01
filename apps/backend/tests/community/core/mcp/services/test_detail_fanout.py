"""Tests for the shared MCP detail fan-out.

Both ``MCPSyncService._sync_mcp_details`` (collects its own list) and
``sync_mcp_details_for_bot`` (takes a caller-supplied desired state) deliver a
batch to one device through this helper, so the bound, the per-entry exception
handling and the success/failure partition live here.
"""
import asyncio

import pytest

from agentclaw.community.core.mcp.services.detail_fanout import (
    fan_out_mcp_details,
    server_code_of,
)


def _mcps(n):
    return [{"server_code": f"mcp.s{i}"} for i in range(n)]


class TestServerCodeOf:
    def test_reads_both_spellings_and_reports_absence(self):
        # MCP Center answers camelCase; internal flow uses snake_case.
        assert server_code_of({"server_code": "a"}) == "a"
        assert server_code_of({"serverCode": "b"}) == "b"
        assert server_code_of({"name": "no code"}) == ""


class TestFanOutMcpDetails:
    @pytest.mark.asyncio
    async def test_partitions_successes_and_failures(self):
        async def push(mcp):
            return mcp["server_code"] != "mcp.s1"

        successes, failures = await fan_out_mcp_details(
            mcps=_mcps(3), push_one=push, concurrency=10, bot_id="bot1",
        )

        assert [m["server_code"] for m in successes] == ["mcp.s0", "mcp.s2"]
        assert [m["server_code"] for m in failures] == ["mcp.s1"]

    @pytest.mark.asyncio
    async def test_delivery_overlaps_but_stays_within_the_bound(self):
        inflight = peak = 0

        async def push(_mcp):
            nonlocal inflight, peak
            inflight += 1
            peak = max(peak, inflight)
            await asyncio.sleep(0.01)
            inflight -= 1
            return True

        successes, failures = await fan_out_mcp_details(
            mcps=_mcps(12), push_one=push, concurrency=5, bot_id="bot1",
        )

        assert peak == 5, "serial delivery would peak at 1; the bound caps it at 5"
        assert len(successes) == 12 and not failures

    @pytest.mark.asyncio
    async def test_entry_without_server_code_fails_without_being_pushed(self):
        pushed = []

        async def push(mcp):
            pushed.append(mcp)
            return True

        successes, failures = await fan_out_mcp_details(
            mcps=[{"server_code": "mcp.s0"}, {"name": "no code"}],
            push_one=push, concurrency=10, bot_id="bot1",
        )

        assert [m["server_code"] for m in successes] == ["mcp.s0"]
        assert failures == [{"name": "no code"}]
        assert pushed == [{"server_code": "mcp.s0"}]

    @pytest.mark.asyncio
    async def test_one_raising_entry_does_not_stop_the_others(self):
        pushed = []

        async def push(mcp):
            pushed.append(mcp["server_code"])
            if mcp["server_code"] == "mcp.s1":
                raise RuntimeError("device refused the payload")
            return True

        successes, failures = await fan_out_mcp_details(
            mcps=_mcps(3), push_one=push, concurrency=10, bot_id="bot1",
        )

        assert sorted(pushed) == ["mcp.s0", "mcp.s1", "mcp.s2"]
        assert [m["server_code"] for m in failures] == ["mcp.s1"]
        assert len(successes) == 2

    @pytest.mark.asyncio
    async def test_cancellation_propagates_rather_than_counting_as_a_failure(self):
        async def push(mcp):
            if mcp["server_code"] == "mcp.s1":
                raise asyncio.CancelledError()
            return True

        with pytest.raises(asyncio.CancelledError):
            await fan_out_mcp_details(
                mcps=_mcps(2), push_one=push, concurrency=10, bot_id="bot1",
            )

    @pytest.mark.asyncio
    async def test_empty_batch_is_a_no_op(self):
        async def push(_mcp):  # pragma: no cover - must never be called
            raise AssertionError("nothing to deliver")

        assert await fan_out_mcp_details(
            mcps=[], push_one=push, concurrency=10, bot_id="bot1",
        ) == ([], [])
