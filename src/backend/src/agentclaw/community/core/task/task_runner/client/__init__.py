"""task_runner integration 子模块:单 bot(Open API)/协作群(BCS)/bbs 真实执行接入。

组合根 ``build_integration`` 装配 double(singlebox)/real(corp 覆写);真实 wiring 属 corp adapter。
"""
from __future__ import annotations

import threading

from agentclaw.community.core.task.task_runner.client.double.double_bcs_client import _DoubleBcsClient
from agentclaw.community.core.task.task_runner.client.double.double_context_provider import (
    _DoubleApiKeyProvider, _DoubleContextProvider,
)
from agentclaw.community.core.task.task_runner.client.double.double_open_api_bot import _DoubleOpenApiBot
from agentclaw.community.core.task.task_runner.client.double.double_bcs_bot_identity_resolver import (
    _DoubleBcsBotIdentityResolver,
)
from agentclaw.community.core.task.task_runner.client.prompt_formatter import PromptFormatterImpl



def build_integration(*, double: bool, sink, runner=None, poller_thread: bool = True,
                      identity_resolver=None, on_bbs_report=None) -> TaskExecutor:
    # Lazy imports keep the client package importable from modal_executor modules
    # without creating a client -> modal_executor -> client cycle.
    from agentclaw.community.core.task.task_runner.modal_executor.task_executor import TaskExecutor
    from agentclaw.community.core.task.task_runner.modal_executor.task_executor_result_poller import (
        TaskExecutorResultPoller,
    )
    """组合根:double(singlebox)/real(corp 覆写)。返装配好的 TaskExecutor(poller 可选起线程)。
    on_bbs_report:引擎收口回调;corp 适配器接线时应透传引擎 on_bbs_report,确保 bbs 接力走引擎收敛(不退 else 直写)。"""
    if double:
        bot = _DoubleOpenApiBot()
        bcs = _DoubleBcsClient()
        ctx = _DoubleContextProvider()
        identity_resolver = identity_resolver or _DoubleBcsBotIdentityResolver()
    else:
        from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import BcsHttpAdapter
        from agentclaw.community.core.task.task_runner.client.bcs_token_provider import _RealToken  # corp 覆写
        from agentclaw.community.core.task.task_runner.client.open_api_bot_adapter import OpenApiBotAdapter
        from agentclaw.community.core.task.task_runner.client.prompt_formatter import _RunnerContextBuilder
        keys = _DoubleApiKeyProvider()
        bot = OpenApiBotAdapter(keys)
        bcs = BcsHttpAdapter(_RealToken())
        ctx = _RunnerContextBuilder(runner) if runner is not None else _DoubleContextProvider()
    poller = TaskExecutorResultPoller(bot=bot, bcs=bcs)
    poller.set_on_result(sink)
    exe = TaskExecutor(
        bot=bot, bcs=bcs, formatter=PromptFormatterImpl(), context=ctx, sink=sink,
        poller=poller, identity_resolver=identity_resolver, on_bbs_report=on_bbs_report,
    )
    if poller_thread:
        t = threading.Thread(target=poller.run_poll_loop, daemon=True)
        t.start()
    return exe
