"""Transport-neutral dispatch to the selected engine's restart policy."""

from agentclaw.community.core.bot_management.engines.registry import (
    resolve_restart_strategy,
)
from agentclaw.community.core.bot_management.engines.restart_contract import (
    RestartServices,
)


class RestartDispatchMixin:
    async def restart_bot_async(self, **kwargs) -> dict:
        bot = self.get_bot(kwargs["bot_id"], kwargs["user_id"])
        ctx, strategy = resolve_restart_strategy(bot)
        return await strategy.execute_restart(
            ctx,
            self.restart_bot,
            services=RestartServices(
                repository=self._repository,
                task_queue=self._task_queue_service,
                get_bot=self.get_bot,
                template_service=self._template_service,
            ),
            **kwargs,
        )
