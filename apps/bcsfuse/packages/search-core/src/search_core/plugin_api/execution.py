"""Physical execution permits outlive a caller timeout or cancellation."""

from typing import Protocol


class CallExecutor(Protocol):
    async def call(self, function, *args):
        """Run within a physical bound; reject saturation rather than queue work."""
        ...
