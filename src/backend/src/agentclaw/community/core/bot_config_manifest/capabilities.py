"""Capability resolver — one function, two entrances (issue #1469).

``supported_categories`` is a *pure* function over ``(engine_type, bot_type)``:
it reads a table, touches neither the database nor a live container, and is
the single source both the read path (``GET …/config-manifest/capabilities``)
and the write path (``PUT`` validation) consult. W13's create-flow preflight
will call it with request parameters — no bot record exists yet at that point
— which is why the signature may not grow richer inputs; a third axis gets
threaded through the service layer as a *narrow override*, never by widening
this contract.

Fail closed: an engine the table does not know answers unsupported for every
category — rather than defaulted-open and discovered at apply time. The
precedent for that spelling is ``layout_planner``'s capability declaration.

Script's second axis, stated plainly: #935's actual support judgment also
depends on the bot's form factor (LOCAL/singlebox and legacy ARCA-direct
bots cannot run one even on an otherwise-supported engine). This table
answers the *engine* half; the per-bot half is #935's ``resolve_support``,
which the service consults wherever a bot record exists. The two are merged
in one place (the service), so no caller has to know which half they need.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: ARCA 系——经 ``_build_create_bot_payload`` 组装启动命令的单容器引擎家族
#: （work-items 术语表）。script 走 #935 启动链,仅这条家族。
ARC_ENGINES = frozenset({"openclaw", "claude_code", "aicoding", "hermes", "moltis"})

#: 第一期有物化器落地的类目。``engine_config`` 被排除在第一期之外
#: （X2/T3:teclaw 创建时序未定,且无人交付物化器）——声明即拒,不是
#: 静默存下等待某个永远不来的 apply;``cli_tools`` 同理,直到 W9 落地
#: 翻开。fail closed。
_PHASE1_APPLIED = frozenset({"mcp", "resources", "skills", "identity"})

#: 六类全表,含未落地类目——能力回答必须把「不支持」也讲清楚。
_ALL_CATEGORIES: tuple[str, ...] = (
    "mcp",
    "resources",
    "skills",
    "engine_config",
    "identity",
    "cli_tools",
)

_ENGINE_CONFIG_REASON = (
    "engine_config is out of phase 1 (teclaw create-time sequencing T3 unresolved; "
    "no materializer delivered)"
)
_CLI_TOOLS_REASON = "cli_tools is delivered by W9 (deferred; PATH injection A2)"
_SCRIPT_TECLAW_REASON = (
    "teclaw containers have no start-command channel — scripts write-refused "
    "(#935 semantics)"
)
_UNKNOWN_ENGINE_REASON = "unknown engine: unsupported, fail closed"


@dataclass(frozen=True)
class CategorySupport:
    """逐类目支持表;每个 ``False`` 必带 reason(否则调用方无从解释拒绝)。"""

    categories: dict[str, bool] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)

    @property
    def script_supported(self) -> bool:
        return self.categories.get("script", False)

    def as_public(self) -> dict[str, object]:
        """API 形态:``{category: bool, reasons: {...}}``,False 才带 reason."""
        return {
            "categories": dict(self.categories),
            "reasons": {
                category: reason
                for category, reason in self.reasons.items()
                if not self.categories.get(category, False)
            },
        }


def supported_categories(engine_type: str, bot_type: str) -> CategorySupport:
    """``(engine_type, bot_type)`` → 逐类目支持表。纯函数,读表,不触库不碰容器。

    ``bot_type`` 在 v1 的表里不产生分支(personal/service 两类 bot 的配置能力
    相同,#926 已按此口径)——签名保留它是刻意的:W13 的参数入口只有这两个
    维度可用,晚于此再扩大函数入参只会让两个调用方分叉。
    未知引擎 → 全 False(#935 教训:fail closed,不静默)。
    """
    categories: dict[str, bool] = {}
    reasons: dict[str, str] = {}

    if engine_type not in ARC_ENGINES and engine_type != "teclaw":
        # 桌面/未知/未来引擎——包括一期的 desktop:matrix 标 manifest 可行但
        # 交付通道需 owner 确认(O2),在此仍按 fail closed 报不支持,确认后翻。
        for category in _ALL_CATEGORIES:
            categories[category] = False
            reasons[category] = _UNKNOWN_ENGINE_REASON
        categories["script"] = False
        reasons["script"] = _UNKNOWN_ENGINE_REASON
        return CategorySupport(categories=categories, reasons=reasons)

    for category in _ALL_CATEGORIES:
        if category in _PHASE1_APPLIED:
            categories[category] = True
        else:
            categories[category] = False
            reasons[category] = (
                _ENGINE_CONFIG_REASON if category == "engine_config" else _CLI_TOOLS_REASON
            )

    if engine_type in ARC_ENGINES:
        categories["script"] = True
    else:  # teclaw
        categories["script"] = False
        reasons["script"] = _SCRIPT_TECLAW_REASON

    return CategorySupport(categories=categories, reasons=reasons)


def identity_file_whitelist(engine_type: str) -> frozenset[str] | None:
    """该引擎允许声明的 identity 文件类型集合;``None`` = 未知引擎,一律拒。

    与 ``core/services/identity.py`` 的两份常量同一口径——W1 只引用不复制
    (两处白名单必然漂移)。claude_code 引擎目前仅允许 CLAUDE.md。
    """
    from agentclaw.community.core.services.identity import (
        CLAUDE_CODE_IDENTITY_FILES,
        VALID_IDENTITY_FILES,
    )

    if engine_type == "claude_code":
        return frozenset(CLAUDE_CODE_IDENTITY_FILES)
    if engine_type in ARC_ENGINES or engine_type == "teclaw":
        return frozenset(VALID_IDENTITY_FILES)
    return None


#: 受保护名单——无论是否在引擎白名单里,manifest 对这两个文件没有管辖权
#: (D2 覆盖语义的唯一例外)。identity 物化器(W5)同样消费它。
RESERVED_IDENTITY_FILES = frozenset({"MEMORY.md", "IDENTITY.md"})
