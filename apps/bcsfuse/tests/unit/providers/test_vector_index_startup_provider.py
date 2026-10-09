import pytest
from src.providers.public.vector_index_startup_provider import (
    VectorIndexStartupProvider,
)


class StartupSpy:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def initialize(self) -> None:
        self.events.append("delegate.initialize")

    async def shutdown(self) -> None:
        self.events.append("delegate.shutdown")


class VectorStoreSpy:
    def __init__(self, events: list[str], *, fail_rebuild: bool = False) -> None:
        self.events = events
        self.fail_rebuild = fail_rebuild

    def rebuild_from_backend(self) -> None:
        self.events.append("vector.rebuild")
        if self.fail_rebuild:
            raise RuntimeError("rebuild failed")

    def close(self) -> None:
        self.events.append("vector.close")


@pytest.mark.asyncio
async def test_rebuild_runs_after_delegate_initialization() -> None:
    events: list[str] = []
    provider = VectorIndexStartupProvider(
        StartupSpy(events),
        VectorStoreSpy(events),
    )

    await provider.initialize()
    await provider.shutdown()

    assert events == [
        "delegate.initialize",
        "vector.rebuild",
        "vector.close",
        "delegate.shutdown",
    ]


@pytest.mark.asyncio
async def test_rebuild_failure_rolls_back_delegate_and_blocks_startup() -> None:
    events: list[str] = []
    provider = VectorIndexStartupProvider(
        StartupSpy(events),
        VectorStoreSpy(events, fail_rebuild=True),
    )

    with pytest.raises(RuntimeError, match="rebuild failed"):
        await provider.initialize()

    assert events == [
        "delegate.initialize",
        "vector.rebuild",
        "delegate.shutdown",
    ]
