"""Rule 25 契约校验（mypy 结构子类型）：3 个新引擎的 prod/local/noop/mock 变体
必须结构满足 ``BotEngineAdapter`` Protocol。赋值即触发 mypy 检查。
"""

from secbaas.community.plugins.bot.engine_adapter.aicoding import (
    MockAICodingAdapter,
    NoopAICodingAdapter,
)
from secbaas.community.plugins.bot.engine_adapter.aicoding.real import AICodingAdapter
from secbaas.community.plugins.bot.engine_adapter.claude_code import (
    MockClaudeCodeAdapter,
    NoopClaudeCodeAdapter,
)
from secbaas.community.plugins.bot.engine_adapter.claude_code.real import (
    ClaudeCodeAdapter,
)
from secbaas.community.plugins.bot.engine_adapter.hermes import (
    MockHermesAdapter,
    NoopHermesAdapter,
)
from secbaas.community.plugins.bot.engine_adapter.hermes.real import HermesAdapter
from secbaas.community.spi.bot.engine_adapter import BotEngineAdapter

# aicoding
_ai_real: BotEngineAdapter = AICodingAdapter()
_ai_noop: BotEngineAdapter = NoopAICodingAdapter()
_ai_mock: BotEngineAdapter = MockAICodingAdapter()

# claude_code
_cc_real: BotEngineAdapter = ClaudeCodeAdapter()
_cc_noop: BotEngineAdapter = NoopClaudeCodeAdapter()
_cc_mock: BotEngineAdapter = MockClaudeCodeAdapter()
