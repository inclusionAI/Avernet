"""Tests for AvernetAgent full lifecycle using a FakeAvernetClient (no HTTP)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from agentcompute.community.plugins.agents._avernet import AvernetAgent, _SafeFormatDict
from agentcompute.community.spi import AgentContext, AgentSpec


class FakeAvernetClient:
    def __init__(self) -> None:
        self.created_manifests: list[str] = []
        self.deleted_bot_ids: list[str] = []
        self.streamed_messages: list[str] = []
        self._stream_chunks = ["Hello", " world"]

    def create_bot_with_manifest(self, manifest_yaml: str) -> str:
        self.created_manifests.append(manifest_yaml)
        self._stream_bot_id = "bot-fake-123"
        return "bot-fake-123"

    def poll_status(self, bot_id: str, timeout: float, interval: float) -> str:
        return "READY"

    def stream_chat(self, bot_id: str, message: str) -> Iterator[str]:
        self.streamed_messages.append(message)
        yield from self._stream_chunks

    def delete_bot(self, bot_id: str) -> None:
        self.deleted_bot_ids.append(bot_id)


class _StreamErrorClient(FakeAvernetClient):
    def stream_chat(self, bot_id: str, message: str) -> Iterator[str]:
        raise RuntimeError("stream failed")


class _CreateErrorClient(FakeAvernetClient):
    def create_bot_with_manifest(self, manifest_yaml: str) -> str:
        raise RuntimeError("create failed")


class _DeleteErrorClient(FakeAvernetClient):
    def delete_bot(self, bot_id: str) -> None:
        raise RuntimeError("delete failed")


def _make_agent(
    spec: AgentSpec | None = None,
    client: FakeAvernetClient | None = None,
    template: str = "name: test",
) -> tuple[AvernetAgent, FakeAvernetClient]:
    spec = spec or AgentSpec(name="searcher", role="finds things")
    client = client or FakeAvernetClient()
    agent = AvernetAgent(
        spec=spec,
        client=client,
        manifest_template=template,
        poll_timeout=1.0,
        poll_interval=0.001,
    )
    return agent, client


def _ctx(goal: str = "hello") -> AgentContext:
    return AgentContext(node_id="n1", goal=goal)


def test_happy_path_creates_streams_deletes() -> None:
    agent, fake = _make_agent()
    agent.setup()
    result = agent.execute(_ctx("hello"))
    agent.teardown()

    assert len(fake.created_manifests) == 1
    assert "bot-fake-123" in fake.deleted_bot_ids
    assert result.output == "Hello world"


def test_teardown_runs_even_when_execute_raises() -> None:
    fake = _StreamErrorClient()
    agent, _ = _make_agent(client=fake)
    agent.setup()

    with pytest.raises(RuntimeError, match="stream failed"):
        agent.execute(_ctx())
    agent.teardown()
    assert "bot-fake-123" in fake.deleted_bot_ids


def test_teardown_noop_when_setup_raised() -> None:
    fake = _CreateErrorClient()
    agent, _ = _make_agent(client=fake)

    with pytest.raises(RuntimeError, match="create failed"):
        agent.setup()
    agent.teardown()
    assert fake.deleted_bot_ids == []


def test_halt_deletes_bot() -> None:
    agent, fake = _make_agent()
    agent.setup()
    agent.halt()
    assert "bot-fake-123" in fake.deleted_bot_ids


def test_manifest_placeholder_substitution() -> None:
    spec = AgentSpec(name="searcher", role="searcher")
    agent, fake = _make_agent(
        spec=spec,
        template="role={role} goal={goal}",
    )
    agent.setup()
    agent.execute(_ctx("hello"))

    assert "role=searcher" in fake.created_manifests[0]
    assert "goal=hello" in fake.streamed_messages[0]


def test_teardown_swallows_delete_error() -> None:
    fake = _DeleteErrorClient()
    agent, _ = _make_agent(client=fake)
    agent.setup()
    agent.teardown()


def test_halt_swallows_delete_error() -> None:
    fake = _DeleteErrorClient()
    agent, _ = _make_agent(client=fake)
    agent.setup()
    # MUST not raise — halt failure must not cascade
    agent.halt()


def test_execute_raises_when_setup_did_not_assign_bot() -> None:
    fake = _CreateErrorClient()
    agent, _ = _make_agent(client=fake)
    with pytest.raises(RuntimeError, match="create failed"):
        agent.setup()
    # bot_id stays None; execute MUST raise before any client call
    with pytest.raises(RuntimeError, match="without a READY bot"):
        agent.execute(_ctx())


def test_safe_format_dict_passes_through_unknown_keys() -> None:
    d = _SafeFormatDict(role="searcher")
    assert d["role"] == "searcher"
    assert "instructions={instructions}" == "instructions={instructions}".format_map(d)
    assert d["nonexistent"] == "{nonexistent}"
