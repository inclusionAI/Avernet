"""The closing step of a platform-managed teclaw apply (W8, plan K-5).

After every category has been written into platform state, the running
container — if there is one — is handed the whole artifact once, through the
same whole-artifact delivery every runtime edit on teclaw takes
(``TeclawDeviceSyncService.sync_symlinks``, which ignores its argument and
recomposes). A bot with no live binding is not an error: provisioning will
compose the first artifact from the state just written, which is the point of
the ``RECORD_PRE_PROVISION`` sequence.

The device graph is reached through two thunks rather than imported: this
package does not depend on ``core.devices``, and the redeliver needs exactly
one resolve and one dispatch, which DI binds.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from agentclaw.community.core.bot_config_manifest.apply.context import ApplyContext
from agentclaw.community.log import get_logger

logger = get_logger()

#: ``(bot_id, owner_id) -> DeviceContext``; raises ``not_bound`` when the bot
#: has no active binding.
ResolveDeviceContext = Callable[[str, str], Any]
#: ``DeviceContext -> DeviceSync`` — the dispatcher's ``dispatch``.
DispatchDeviceSync = Callable[[Any], Any]


class TeclawRedeliver:
    """One whole-artifact redeliver to a bound bot; nothing to an unbound one."""

    def __init__(
        self,
        *,
        resolve: ResolveDeviceContext,
        dispatch: DispatchDeviceSync,
        not_bound: type[BaseException],
    ) -> None:
        self._resolve = resolve
        self._dispatch = dispatch
        self._not_bound = not_bound

    async def __call__(self, ctx: ApplyContext) -> Optional[str]:
        try:
            device = await asyncio.to_thread(self._resolve, ctx.bot_id, ctx.owner_id)
        except self._not_bound:
            # No container yet: provisioning composes the first artifact.
            return None
        # Blocking HTTP to the container, off the event loop like every other
        # device write the materialisers make.
        result = await asyncio.to_thread(
            lambda: self._dispatch(device).sync_symlinks([])
        )
        if isinstance(result, dict) and result.get("success") is False:
            message = str(result.get("message") or "delivery failed")
            logger.warning(
                "[manifest_apply] redeliver failed: bot_id=%s apply_id=%s: %s",
                ctx.bot_id, ctx.apply_id, message,
            )
            return f"artifact could not be redelivered to the running container: {message}"
        return None


__all__ = ["DispatchDeviceSync", "ResolveDeviceContext", "TeclawRedeliver"]
