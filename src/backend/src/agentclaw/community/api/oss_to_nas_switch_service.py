"""Service API Protocol for OSS → NAS switch / rollback workflows."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class OssToNasSwitchServiceProtocol(Protocol):
    """Service API for switching bot storage between OSS and NAS."""

    async def switch_one(
        self, staff_no: str, bot_id: str, env: Optional[str] = None
    ) -> Dict[str, Any]: ...

    async def rollback_one(
        self, staff_no: str, bot_id: str, env: Optional[str] = None
    ) -> Dict[str, Any]: ...

    async def batch_switch_with_concurrency(
        self,
        records: List[Dict[str, Any]],
        concurrency: int,
        *,
        env: str,
    ) -> Dict[str, Any]: ...
