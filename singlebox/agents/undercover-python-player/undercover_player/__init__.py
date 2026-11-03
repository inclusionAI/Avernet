"""谁是卧底 · 非 OpenClaw 玩家 Bot（纯 Python BCN WebSocket 引擎）。

Avernet issue #2611：让不同引擎的 agent 坐上同一张“谁是卧底”牌桌。
本包是「代码级引擎」实现：直接按 BCN 协议（V3）通过 WebSocket 接入 BCS，
自行实现登录握手、心跳、chat.send 节点任务处理、chat.abort 精确取消、
迟到消息屏蔽，以及 V3 canonical chat run event 回传。

玩家行为规则（发言 ≤25 字 / 按轮次控钝度 / 投票只交「我投N号」或「我弃权」）
由 format_rules 与 engine 承载，保证输出格式能被主持人脚本 undercover.py 解析。

参考：
  docs/bot-integration.zh-CN.md
  docs/undercover-game-tutorial.zh-CN.md
  singlebox/agents/6bots_undercover_game_profile/referee/.../undercover.py
"""
from .message_model import NodeTask, NodePhase  # noqa: F401
from .format_rules import (  # noqa: F401
    MAX_SPEECH_CHARS,
    MAX_VOTE_CHARS,
    check_own_word_leak,
    format_vote,
)
from .engine import PeterPlayerEngine  # noqa: F401
from .agent import run_player  # noqa: F401

__version__ = "0.1.0"