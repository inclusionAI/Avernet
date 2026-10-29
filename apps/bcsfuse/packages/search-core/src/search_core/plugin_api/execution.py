"""Optional execution deadlines for synchronous or native async provider calls."""

from typing import Protocol


class CallExecutor(Protocol):
    async def call(self, function, *args):
        """Await async-def providers directly; offload synchronous providers.

        Return their result or raise on failure, timeout or cancellation.
        An async provider must propagate cancellation and must not block the loop.
        """
        ...
