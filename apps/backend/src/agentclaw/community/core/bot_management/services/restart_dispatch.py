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

    def get_bot_status(self, bot_id: str, user_id: str) -> dict:
        """Return a Bot-shaped status view; never change stored runtime state."""
        bot = self.get_bot(bot_id, user_id)
        ctx, strategy = resolve_restart_strategy(bot)
        return strategy.project_restart_status(
            ctx, bot, task_queue=self._task_queue_service
        )
