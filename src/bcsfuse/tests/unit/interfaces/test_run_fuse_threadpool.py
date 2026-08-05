import asyncio
import threading

from src.interfaces.api.fusion_parity_routes import _run_fuse


class _StubService:
    def __init__(self) -> None:
        self.called_thread = None

    def fuse(self, request, group_id=None):
        self.called_thread = threading.get_ident()
        return "fused"


def test_run_fuse_offloads_to_a_worker_thread() -> None:
    async def main() -> None:
        loop_thread = threading.get_ident()
        service = _StubService()
        result = await _run_fuse(service, None, group_id="g-1")
        assert result == "fused"
        assert service.called_thread is not None
        assert service.called_thread != loop_thread

    asyncio.run(main())