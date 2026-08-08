"""D-TOOLS-002 — TOOLS.md MCP 调用规范诊断.

针对用户配置的 MCP 进行独立检查，判断是否声明了具体 MCP 的调用规范，
对应文档中"三、场景与工具映射速查"和"四、高频 MCP 调用规范"。
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests

from agentclaw.community.core.harness.diagnostics.base import Diagnostic, DiagnosticContext
from agentclaw.community.core.harness.models import Finding, Severity, score_to_result
from agentclaw.community.core.harness.services.llm import DIAGNOSTIC_MAX_TOKENS
from agentclaw.community.core.harness.services.llm_response_parser import (
    _LLM_DISABLED_MARKER,
    _NO_ISSUE_MARKERS,
    _extract_score,
    _extract_summary_and_message,
)

if TYPE_CHECKING:
    from agentclaw.community.di.config import KbConfig

logger = logging.getLogger(__name__)

# ── 内网知识库配置 ──────────────────────────────────────────────
# Endpoint / token / KB service names all come from KbConfig (the ``kb`` yaml
# block, corp env overlays). The neutral shipped code embeds no KB. When the
# config is absent or its base_url/token are empty (community build), the KB
# query is skipped and returns [].
_KB_TIMEOUT = 10
_KB_TOP_K = 5
_KB_RANKING_THRESHOLD = 0.1


def _query_knowledge_base(question: str, kb_config: "KbConfig | None") -> list[dict]:
    """Query internal knowledge base for MCP-related context.

    Always uses prod env because the KB instance only exists there.
    Returns a list of result dicts (each with 'q' and 'a' keys).
    Returns empty list on any failure or when the KB is not configured.
    """
    logger.info("[D-TOOLS-002] KB query start: question=%r", question)
    if not kb_config or not kb_config.base_url or not kb_config.token:
        logger.info(
            "[D-TOOLS-002] KB not configured (kb.base_url/kb.token empty), "
            "skipping query"
        )
        return []
    request_body = {
        "functionName": kb_config.function_name,
        "env": "prod",
        "params": {
            "instanceName": kb_config.instance_name,
            "token": kb_config.token,
            "interfaceName": kb_config.interface_name,
            "userName": "agentclaw",
            "userId": "agentclaw",
            "env": "prod",
            "param": {
                "question": question,
                "topK": str(_KB_TOP_K),
                "rankingThreshold": str(_KB_RANKING_THRESHOLD),
                "rankingModel": "bge-reranker-large",
                "threshold": str(_KB_RANKING_THRESHOLD),
                "skipBuildPromptContext": "true",
                "queryMethods": '["VECTOR","TEXT"]',
                "enableCache": "false",
            },
        },
    }
    try:
        resp = requests.post(
            kb_config.base_url,
            json=request_body,
            headers={"Content-Type": "application/json"},
            timeout=_KB_TIMEOUT,
        )
        result = resp.json()
        logger.info(
            "[D-TOOLS-002] KB query response: success=%s data.code=%s",
            result.get("success"),
            result.get("data", {}).get("code", ""),
        )
        if not result.get("success"):
            logger.warning(
                "[D-TOOLS-002] KB query failed: success=false, log=%s",
                result.get("log", "unknown"),
            )
            return []
        data = result.get("data", {})
        data_code = data.get("code", "")
        if data_code != "OK":
            logger.warning(
                "[D-TOOLS-002] KB query returned non-OK: code=%s message=%s",
                data_code,
                data.get("message", ""),
            )
            return []
        answer_list = data.get("runResult", {}).get("answer", [])
        parsed: list[dict] = []
        for item_str in answer_list:
            try:
                item = json.loads(item_str) if isinstance(item_str, str) else item_str
                parsed.append(item)
            except (json.JSONDecodeError, TypeError):
                continue
        logger.info("[D-TOOLS-002] KB query success: question=%r results=%d", question, len(parsed))
        return parsed
    except Exception:
        logger.warning(
            "[D-TOOLS-002] KB query request failed for question=%r",
            question,
            exc_info=True,
        )
        return []


_RERANK_THRESHOLD = 0.15
_TOP_K_PER_MCP = 2


def _parse_kb_answer(raw_answer: str) -> str | None:
    """Parse KB answer field, extracting the meaningful content.

    The 'a' field may contain 'query:XXX\\nanswer:YYY' format.
    Returns the YYY part (the actual answer), or the full text if no prefix.
    Returns None if empty after cleanup.
    """
    text = raw_answer.strip()
    if not text:
        return None
    for line in text.split("\n"):
        line = line.strip()
        if line.lower().startswith("answer:"):
            return line[len("answer:"):].strip()
    return text


def _fetch_kb_context_for_mcps(
    mcp_details: list[dict], kb_config: "KbConfig | None"
) -> str:
    """Query internal KB for each MCP, deduplicate by q, return formatted context.

    Uses MCP description as query. Takes top-K results per MCP (sorted by
    rerankScore), then deduplicates globally. Always queries the prod KB.
    Returns a formatted string section to append to the LLM prompt, or empty string.
    """
    logger.info("[D-TOOLS-002] KB context fetch start: mcp_count=%d", len(mcp_details))
    seen_queries: set[str] = set()
    kb_entries: list[tuple[str, str, float]] = []  # (term, description, score)
    filtered_by_score = 0

    for mcp in mcp_details:
        description = mcp.get("description", "")
        if not description:
            continue
        results = _query_knowledge_base(description, kb_config)
        # Take top-K per MCP that pass the minimum threshold
        qualified = [
            r for r in results
            if r.get("rerankScore", 0) >= _RERANK_THRESHOLD
        ]
        qualified.sort(key=lambda r: r.get("rerankScore", 0), reverse=True)
        for r in qualified[:_TOP_K_PER_MCP]:
            q = r.get("q", "")
            score = r.get("rerankScore", 0)
            if not q or q in seen_queries:
                continue
            seen_queries.add(q)
            parsed = _parse_kb_answer(r.get("a", ""))
            if parsed:
                kb_entries.append((q, parsed, score))

        filtered_count = len(results) - len(qualified)
        if filtered_count > 0:
            filtered_by_score += filtered_count

    logger.info(
        "[D-TOOLS-002] KB context fetch done: entries=%d filtered_by_score=%d",
        len(kb_entries), filtered_by_score,
    )

    if not kb_entries:
        return ""

    # Sort by score descending for readability
    kb_entries.sort(key=lambda x: x[2], reverse=True)
    lines = ["--- 内网知识库参考（MCP 相关术语与服务说明） ---"]
    for term, desc, _score in kb_entries:
        lines.append(f"- **{term}**: {desc}")
    lines.append("")
    return "\n".join(lines)


# ── Verified MCP call guides ──────────────────────────────────────
# Only MCPs whose guides have been manually verified are listed here.
# key = server_code, value = path to the verified markdown guide (relative to this file).
_VERIFIED_MCP_GUIDES: dict[str, str] = {
    "mcp.ant.faas.skylarkmcpserver.skylarkmcpserver": "mcp_guides/skylarkmcpserver.md",
    "mcp.ant.faas.pydataphin.assets": "mcp_guides/pydataphin.md",
    "mcp.ant.rpc.dpagent.dataprocess": "mcp_guides/dpagent.md",
    "mcp.ant.riskfaasai.riskfaasai-case-mcp-server": "mcp_guides/uctmcptools.md",
}

_GUIDES_DIR = Path(__file__).parent


def _load_verified_guide(server_code: str) -> str | None:
    """Load a verified MCP guide file. Returns None if not found."""
    rel_path = _VERIFIED_MCP_GUIDES.get(server_code)
    if not rel_path:
        return None
    guide_file = _GUIDES_DIR / rel_path
    try:
        return guide_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("[D-TOOLS-002] Verified guide not found: %s", guide_file)
        return None
    except Exception:
        logger.warning("[D-TOOLS-002] Failed to read verified guide: %s", guide_file)
        return None


# ── prompt budget guard ───────────────────────────────────────
# #717 handed the diagnostic LLM each tool's *full* ``inputSchema`` (nested JSON
# Schema: ``$schema``/deep ``properties``/long per-param descriptions) for every
# configured MCP tool. A bot with many MCPs — e.g. ~88 tools across 10 MCPs —
# produced a 50–100k-token user message that antchat's gateway couldn't process
# inside its ~90s window → ``RemoteProtocolError`` on every retry → the
# ``[llm disabled]`` sentinel → a spurious "LLM执行异常(请重试)". We compress
# each tool to a flat param table (name/type/required/one-line desc) and cap the
# number of tools per MCP in the prompt so the message stays small enough to
# finish inside the window. The full ``inputSchema`` is still read for bucketing
# (verified/schema/unreachable) and the ``has_recoverable`` advisory check —
# only the *prompt payload* is slimmed.
_MAX_TOOLS_PER_MCP_IN_PROMPT = 20
_PARAM_DESC_MAX_CHARS = 120
_TOOL_DESC_MAX_CHARS = 200


def _compact_tool_for_prompt(tool: dict) -> str:
    """Render one MCP tool as a single prompt line.

    Drops the nested ``inputSchema`` JSON and emits a flat one-liner the
    diagnostic LLM can transcribe a non-fabricated call spec from:
    ``name(param:type*=required, …) — one-line desc``. Tools with no schema
    render as ``name — desc``. One line per tool keeps an 88-tool MCP list to
    a few KB instead of tens of KB of indented JSON.
    """
    schema = tool.get("inputSchema")
    params: list[str] = []
    if isinstance(schema, dict):
        props = schema.get("properties")
        required = schema.get("required") or []
        required_set = set(required) if isinstance(required, (list, tuple)) else set()
        if isinstance(props, dict):
            for pname, pdef in props.items():
                if not isinstance(pdef, dict):
                    continue
                ptype = pdef.get("type", "") or ""
                star = "*" if pname in required_set else ""
                params.append(f"{pname}:{ptype}{star}")
    name = tool.get("name", "")
    sig = f"{name}({', '.join(params)})" if params else name
    t_desc = (tool.get("description") or "").strip()
    t_desc = t_desc.split("\n", 1)[0][:_TOOL_DESC_MAX_CHARS]
    return f"{sig} — {t_desc}" if t_desc else sig


def _compact_mcp_tools_for_prompt(mcp: dict) -> tuple[list[str], int]:
    """Return ``(one-line tool strings, count of named tools omitted past the cap)``.

    Caps each MCP's tools at ``_MAX_TOOLS_PER_MCP_IN_PROMPT`` so a 27-tool MCP
    doesn't dominate the message; the omitted count is surfaced separately so
    the caller can annotate it (``…另N个``). Tools without a name are dropped.
    Reads the *original* tools list — not a pre-slimmed copy — so the bucketing
    and ``has_recoverable`` checks that run against ``mcp_details`` still see
    the unredacted ``inputSchema``.
    """
    tools = mcp.get("tools") or []
    if not isinstance(tools, list):
        return [], 0
    named = [t for t in tools if isinstance(t, dict) and t.get("name")]
    kept = named[:_MAX_TOOLS_PER_MCP_IN_PROMPT]
    compacted = [_compact_tool_for_prompt(t) for t in kept]
    return compacted, len(named) - len(kept)


def _format_mcp_block(mcp: dict, *, include_guide: bool) -> str:
    """Render one MCP as a compact text block for the prompt.

    Single ``## server_code (name): description`` header plus a ``tools:``
    list of one-line tool strings, and (when ``include_guide``) the verbatim
    ``verified_guide``. Far smaller than the previous indented-JSON dump while
    keeping every param name/type/required the LLM needs to transcribe a call
    spec.
    """
    server_code = mcp.get("server_code", "")
    name = mcp.get("name", "")
    desc = (mcp.get("description") or "").strip().split("\n", 1)[0]
    header = f"## {server_code} ({name}): {desc}" if desc else f"## {server_code} ({name})"
    lines = [header]
    guide = mcp.get("verified_guide") if include_guide else None
    if guide:
        lines.append(f"verified_guide:\n{guide}")
    compacted, omitted = _compact_mcp_tools_for_prompt(mcp)
    if compacted:
        lines.append("tools:")
        lines.extend(f"- {t}" for t in compacted)
        if omitted:
            lines.append(f"- …另{omitted}个未列出")
    return "\n".join(lines)


# ── batched prompt mode ───────────────────────────────────────
# Even with the compact rendering above, one LLM call carrying *every*
# configured MCP grows with bot complexity, while antchat's gateway kills any
# request it can't answer inside ~90s (88-tool bot: 5×90s
# ``RemoteProtocolError`` on a ~19k-char prompt, while D-TOOLS-001-scale
# prompts of a few KB succeed). Batching decouples prompt size from MCP
# count: each call carries at most ``_BATCH_MAX_MCPS`` MCPs /
# ``_BATCH_CHAR_BUDGET`` chars of MCP data. Single-batch bots keep the
# pre-batching prompt byte-for-byte. Batches run concurrently — llm.py's own
# semaphore allows 10, so ``_BATCH_CONCURRENCY`` is the only limiter; 3 is
# the safe default until antchat's rate limit is known (bump to 5 if clean).
_BATCH_MAX_MCPS = 3
_BATCH_CHAR_BUDGET = 4000
_BATCH_CONCURRENCY = 3
# MCPs with more tools than this get a batch to themselves: a 3-large-MCP batch
# (e.g. 27+19+19 tools) piles ~3k chars of signatures on top of the TOOLS.md
# source and blows antchat's 90s window. Small MCPs still pack 3-per-batch.
_LARGE_MCP_TOOL_THRESHOLD = 12

_BUCKET_ORDER = ("verified", "schema", "unreachable")
_BUCKET_HEADERS = {
    "verified": "✅ 已验证 MCP（verified_guide 不为空，引用原文生成调用规范）",
    "schema": "🛠 可据参数表转录 MCP（无 verified_guide，但工具含 params 参数表：可据参数表转录调用规范，禁止编造示例/命令）",
    "unreachable": "⛔ 无 schema MCP（既无 verified_guide 又无 inputSchema，仅写入映射表，禁止生成调用规范/示例）",
}


def _render_bucketed_blocks(mcp_details: list[dict]) -> list[tuple[str, str, dict]]:
    """Render each enriched MCP as ``(bucket, block_text, mcp_detail)``.

    Bucketing is unchanged from #717: verified → guide verbatim; schema →
    transcribe from params; unreachable → mapping-table row only.
    """
    rendered: list[tuple[str, str, dict]] = []
    for mcp in mcp_details:
        tools = mcp.get("tools") or []
        has_schema = any(
            isinstance(t, dict) and t.get("inputSchema")
            for t in (tools if isinstance(tools, list) else [])
        )
        if mcp.get("verified_guide"):
            rendered.append(("verified", _format_mcp_block(mcp, include_guide=True), mcp))
        elif has_schema:
            rendered.append(("schema", _format_mcp_block(mcp, include_guide=False), mcp))
        else:
            # Unreachable: mapping-table row only (no tools, no schema).
            server_code = mcp["server_code"]
            name = mcp.get("name", "")
            desc = (mcp.get("description") or "").strip().split("\n", 1)[0]
            header = f"## {server_code} ({name}): {desc}" if desc else f"## {server_code} ({name})"
            rendered.append((
                "unreachable",
                header + "\n注意: 该 MCP 既无人工校验调用规范也无工具 inputSchema，"
                "只能在「场景与工具映射速查」表格中列出，注意事项写「调用规范由平台补全中」；"
                "禁止为其生成「MCP 调用规范」子章节或 mcporter call 示例",
                mcp,
            ))
    return rendered


def _pack_mcp_batches(items: list[tuple[str, str, dict]]) -> list[list[tuple[str, str, dict]]]:
    """Greedy-pack rendered MCP blocks into LLM-call batches.

    A batch closes when it already holds an item and adding the next would
    exceed ``_BATCH_MAX_MCPS`` or ``_BATCH_CHAR_BUDGET``; a block larger than
    the budget gets a batch to itself.
    """
    batches: list[list[tuple[str, str, dict]]] = []
    current: list[tuple[str, str, dict]] = []
    current_chars = 0
    for item in items:
        tools = (item[2].get("tools") or [])
        is_large = len(tools) > _LARGE_MCP_TOOL_THRESHOLD
        # Close the current batch before a large MCP (it runs alone to keep
        # that call's output — one full spec draft, not three — inside the
        # 90s window), or when the count/char budget would be exceeded.
        if current and (is_large or len(current) >= _BATCH_MAX_MCPS or current_chars + len(item[1]) > _BATCH_CHAR_BUDGET):
            batches.append(current)
            current, current_chars = [], 0
        current.append(item)
        current_chars += len(item[1])
        # A large MCP occupies its batch alone.
        if is_large:
            batches.append(current)
            current, current_chars = [], 0
    if current:
        batches.append(current)
    return batches


def _render_mcp_sections(batch: list[tuple[str, str, dict]]) -> str:
    """Render one batch's blocks under their bucket section headers (fixed order)."""
    out = ""
    for kind in _BUCKET_ORDER:
        blocks = [block for bucket, block, _ in batch if bucket == kind]
        if blocks:
            out += f"\n--- {_BUCKET_HEADERS[kind]} ---\n" + "\n\n".join(blocks) + "\n"
    return out


def _fetch_kb_quiet(mcp_details: list[dict], kb_config: "KbConfig | None", bot_id: str) -> str:
    """KB context for the given MCPs; ``""`` when unconfigured or on failure."""
    try:
        return _fetch_kb_context_for_mcps(mcp_details, kb_config) or ""
    except Exception:
        logger.warning("[D-TOOLS-002] KB context fetch failed for bot=%s", bot_id, exc_info=True)
        return ""


def _synthesize_batch_responses(results: list[tuple[str | None, list[str]]]) -> str:
    """Merge per-batch LLM responses into one response text for the parser.

    ``results`` holds ``(response_or_None, server_codes)`` per batch; ``None``
    marks a batch whose LLM call raised. Behaviour:

    - every batch failed (raise / empty / sentinel) → return the sentinel so
      the run degrades to the usual LLM01 finding, as an oversized single
      call did before batching;
    - all healthy batches answered no-issue (and nothing failed) → ``无问题``;
    - otherwise: distinct batch summaries joined by "；", drafts concatenated,
      failed batches' server_codes noted as undiagnosed, and an aggregate
      ``max(10, 100 − Σ(100 − batch_score))``. The aggregate re-counts the
      rubric's global section-missing deductions once per batch — biased low
      (conservative for a health check), never inventing a pass.
    """

    def _usable(resp: str | None) -> bool:
        return bool(resp and resp.strip()) and resp.strip() != _LLM_DISABLED_MARKER

    failed_codes = [c for resp, codes in results if not _usable(resp) for c in codes]
    ok_resps = [resp.strip() for resp, _ in results if _usable(resp)]
    if not ok_resps:
        return _LLM_DISABLED_MARKER

    summaries: list[str] = []
    drafts: list[str] = []
    deductions = 0
    for resp in ok_resps:
        if resp.lower() in _NO_ISSUE_MARKERS:
            continue
        score, body = _extract_score(resp)
        summary, message = _extract_summary_and_message(body)
        deductions += 100 - score
        if summary and summary not in summaries:
            summaries.append(summary)
        if message:
            drafts.append(message)

    if failed_codes:
        drafts.append("（以下 MCP 本次未完成诊断（LLM 调用失败），请稍后重试：" + "、".join(failed_codes) + "）")
    if not summaries and not drafts:
        return "无问题"
    if not summaries:
        # Only failures, nothing diagnosed — surface as check-failed, not pass.
        return "\n".join(["部分 MCP 诊断失败", "\n\n".join(drafts), "[SCORE:0]"])
    total = max(10, 100 - deductions)
    return "\n".join(["；".join(summaries), "\n\n".join(drafts), f"[SCORE:{total}]"])


class ToolsMcpFormatDiagnostic(Diagnostic):
    id = "D-TOOLS-002"
    name = "各项 MCP 调用规范诊断"
    severity = "warning"
    file_type = "TOOLS.md"
    suggested_template_ids: list[int] = [2]
    system_prompt = """\
你是一个 Bot 工具配置诊断与优化专家。请对提供的 TOOLS.md 文件，结合用户已配置的 MCP 列表，诊断其中是否为每个 MCP 声明了清晰、可执行的调用规范；如果缺失或不完整，请直接给出可落地的补充建议。

你的目标不是泛泛评价，而是帮助用户把 TOOLS.md 补到"Bot 遇到问题时能直接照着调用 MCP"的程度。

【诊断范围】
只关注 MCP 调用规范，重点检查以下两部分是否存在且质量足够：
1. `## 场景与工具映射速查`
   - 是否建立了"用户意图关键词 -> MCP 服务器全称 -> 推荐工具 -> 注意事项"的映射
   - 是否覆盖用户已配置的主要 MCP，至少让 Bot 知道什么场景该调用哪个 MCP/工具
   - 是否避免只有 MCP 名称、没有工具和场景说明的空泛描述

2. `## MCP 调用规范`
   - 是否针对已经配置 MCP 给出了标准调用模板、推荐工作流、关键参数说明和常见陷阱
   - 是否包含明确、可操作的调用示例
   - 是否指出易错点，如参数嵌套、字段类型、必填项、字符串传递、特殊格式要求等

【判断标准】
出现以下情况时，应判定为存在问题：
- 用户已配置了 MCP，但 TOOLS.md 中没有提到该 MCP 的调用规范
- 提到了 MCP 名称，但没有"适用场景 / 推荐工具 / 注意事项"
- 提到了工具，但没有示例或约束，Bot 仍然无法稳定调用
- 内容过于笼统，无法指导 Agent 实际调用

【数据说明】
你会拿到一份"用户已配置的 MCP 列表"，其中每个 MCP 包含：
- `server_code`: MCP 服务器全称
- `name`: MCP 名称
- `description`: 该 MCP 的功能描述
- `tools`: 该 MCP 提供的工具列表（每个 MCP 最多展示前 20 个工具，超出部分以 `tools_omitted` 标注数量），每项含 `name`（工具名）、`desc`（工具一句话描述）、`params`（参数表：每项含 `name`/`type`/`required`/`desc`，其中 `desc` 为该参数的说明）
- `verified_guide`（可选）：人工校验过的调用规范全文，可直接用于 TOOLS.md
- `kb_context`（可选）：从内网知识库检索到的与 MCP 相关的术语与服务说明，可帮助更准确地理解 MCP 的定位和用途。**必须在"场景与工具映射速查"表格的"注意事项"列中充分利用这些信息**，例如将术语解释作为注意事项的一部分，帮助 Bot 更好地理解和使用该 MCP

**关键规则：按"是否有可落地的调用规范来源"决定调用规范章节**
- `verified_guide` 不为空 → 调用规范已经过人工验证，内容准确可用。修复建议中应直接引用 verified_guide 原文，建议用户粘贴到 TOOLS.md；同时在"场景与工具映射速查"表格中列出该 MCP。
- `verified_guide` 为空、但该 MCP 的工具提供了 `params` 参数表 → **可基于 `params` 转录**该工具的参数规格（参数名/类型/必填/描述）写入"## MCP 调用规范"子章节。**仅转录参数表明确给出的内容，禁止编造**调用示例值、`mcporter call` 命令、业务工作流或易错点等参数表未提供的信息。同时仍在映射表中列出该 MCP。
- `verified_guide` 为空、且工具也没有 `params` 参数表（取不到该 MCP 的工具 schema） → **只**在"场景与工具映射速查"表格列出该 MCP 的映射行，**不得**为其生成"##  MCP 调用规范"子章节，注意事项写"调用规范由平台补全中"。

请充分利用这些信息，在修复建议中引用对应 MCP 的 description 和 tools，帮助用户快速补齐文档。

【输出要求】
- 如果没有发现问题，请输出"无问题"

- 如果发现问题，必须严格按以下格式输出：

第一行：
5-7个字的简短总结，如"映射表缺失""MCP规范不全"

中间部分：
先用简洁中文说明问题，再给出"建议补充内容草案"。
"建议补充内容草案"必须尽量采用 TOOLS.md 可直接粘贴的格式，并优先包含以下三个标题：

## 场景与工具映射速查
- 以 Markdown 表格输出
- 表头固定为：
| 用户意图关键词 | MCP 服务器全称 | 推荐工具 | 注意事项 |
|-------------|--------------|---------|---------|
- 应优先覆盖用户已配置且高频/关键的 MCP
- 推荐工具必须从该 MCP 的 tools 列表中选择或组合，不要编造工具名
- 注意事项可结合 description、工具名称和常见调用约束来写，要求具体、可执行；如果提供了内网知识库参考，必须将相关术语说明融入注意事项中

##  MCP 调用规范
- 本章节允许包含：`verified_guide` 不为空的 MCP（直接引用原文），**以及** `verified_guide` 为空但其工具提供了 `params` 参数表的 MCP（挑推荐工具转录）。
- 每个 MCP 子章节格式（控制 TOOLS.md 体积，避免把所有工具的全部参数都罗列进去）：
  1. **列出该 MCP 的全部工具名**（一行一个或逗号分隔），让 Bot 知道有哪些工具可用；
  2. 再**只挑 2-4 个推荐工具**（据工具描述，选该 MCP 主要场景最高频/关键的）**详列必填参数**（name/type/必填；选填可略）；
  3. 写**注意事项**：必填项提醒、类型/格式约束、易错点。
- ❌ 不要为每个工具都罗列全部参数规格；推荐工具之外的只列工具名即可。
- ❌ 严格禁止：为"既无 `verified_guide` 又无工具 `params`"的 MCP 生成 `### XXX (server_code)` 子章节或调用示例——这类 MCP 只能出现在映射表里。
- ✅ 推荐工具参数：基于 `params` 的 name/type/required 如实转录必填项；不要编造参数表未提供的调用示例值、命令或业务流程。
- 如果 MCP 列表中提供了 verified_guide，请直接引用该内容，用户可原样粘贴到 TOOLS.md。
- 输出是"诊断 + 修复建议"，不是完整重写整份 TOOLS.md，只补最关键、最缺失的部分。

## 内网业务知识补充
- **此章节仅当提供了"内网知识库参考"数据时才输出**，若未提供则跳过
- 将内网知识库中与已配置 MCP 相关的术语与服务说明，整理为 Bot 可直接参考的业务知识
- 每个术语以 `### 术语名` 作为子标题，下方给出该术语的完整说明
- 术语选择标准：只收录与已配置 MCP 有直接关联的条目（如 MCP 涉及的平台、领域、业务概念），无关条目不要收录
- 内容直接引用知识库提供的原文，不要编造或扩展
- 此章节帮助 Bot 在调用 MCP 时理解背后业务含义，减少因不了解业务术语而误用工具的情况

最后一行：
[SCORE:XX]
XX 为 0-100 的整数

评分细则（满分100，从100开始扣分）：
- 完全缺失"场景与工具映射速查"的相关内容：-35分
- 映射表存在但未覆盖已配置的主要MCP：每个未覆盖的MCP -8分
- 映射表行缺少推荐工具或注意事项（仅列MCP名无工具）：每行-4分
- 完全缺失"MCP调用规范"章节：-35分
- MCP调用规范存在但对已配置MCP的覆盖不全：每个缺失的MCP -5分
- 调用规范中缺少可操作的 mcporter call示例：每个MCP -3分
- 调用规范中缺少易错点/陷阱说明：每个MCP -2分
- 最低10分
示例：映射表覆盖一半MCP(-16)，MCP规范仅覆盖1个(-20) → 100 - 16 - 20 = 64

【严格限制】
- 只诊断 MCP 调用规范
- 不得评价通用工具调用纪律（那属于其他诊断）
- 不得评价安全规则
- 不得评价行为边界
- 不得脱离已提供的 MCP 列表空泛发挥
- 不得编造未出现在 tools 列表中的工具名称
- **禁止为"既无 `verified_guide` 又无工具 `params`"的 MCP 生成"MCP 调用规范"子章节**；这类 MCP 只能出现在"场景与工具映射速查"表格中，注意事项栏写"调用规范由平台补全中"
- **禁止编造 `mcporter call` 示例、调用示例值或业务工作流**——仅有 `params` 参数表时只转录参数表给出的参数规格，不得生成参数表未提供的命令/示例

【额外要求】
如果发现问题，你的修复建议中应尽量让用户一眼就能复制到 TOOLS.md：
- 优先输出 Markdown 标题、表格、列表、代码块
- 尽量使用 `## 场景与工具映射速查` 、 `## MCP 调用说明`、`## 内网业务知识补充` 这几个标题原文
- 若某些 MCP 尚未覆盖，应点名指出"缺少 server_code 为 XXX 的 MCP 调用说明"，并结合其 description 和 tools 提示如何补充

"""

    @staticmethod
    def _enrich_mcp_with_detail(mcp: dict, mcp_center: Any) -> dict:
        """Query MCP Center for a single MCP's description and tools.

        Returns an enriched dict with server_code, name, description, tools.
        Falls back to the original mcp dict fields on failure.
        """
        server_code = mcp.get("server_code") or ""
        if not server_code:
            logger.debug("[D-TOOLS-002] Skipping MCP with empty server_code: name=%s", mcp.get("name", ""))
            return {
                "server_code": "",
                "name": mcp.get("name", ""),
                "description": mcp.get("description", ""),
                "tools": [],
                "verified_guide": None,
            }

        try:
            detail = mcp_center.get_mcp_detail(server_code)
            if detail:
                tools = detail.get("tools", [])
                # Keep each tool's description + inputSchema (not just its name) so the
                # diagnostic LLM can transcribe a real, non-fabricated ## MCP 调用规范
                # from the JSON Schema (params/required/types) without a verified_guide.
                tool_details = [
                    {
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "inputSchema": t.get("inputSchema"),
                    }
                    for t in tools
                    if isinstance(t, dict) and t.get("name")
                ]
                logger.info(
                    "[D-TOOLS-002] MCP detail enriched: server_code=%s tools=%d",
                    server_code, len(tool_details),
                )
                return {
                    "server_code": server_code,
                    "name": detail.get("name", server_code),
                    "description": detail.get("description", ""),
                    "tools": tool_details,
                    "verified_guide": None,
                }
            logger.warning("[D-TOOLS-002] MCP detail empty for server_code=%s", server_code)
        except Exception:
            logger.warning(
                "[D-TOOLS-002] get_mcp_detail failed for server_code=%s",
                server_code,
                exc_info=True,
            )

        logger.info("[D-TOOLS-002] MCP enrichment fallback: server_code=%s", server_code)
        return {
            "server_code": server_code,
            "name": mcp.get("name", server_code),
            "description": mcp.get("description", ""),
            "tools": [],
            "verified_guide": None,
        }

    @staticmethod
    def _attach_verified_guide(mcp_detail: dict) -> dict:
        """Attach verified guide content to an enriched MCP detail if available.

        Returns a new dict with the `verified_guide` field populated.
        Does not mutate the input.
        """
        server_code = mcp_detail["server_code"]
        guide = _load_verified_guide(server_code)
        if guide is None:
            logger.debug("[D-TOOLS-002] No verified guide for server_code=%s", server_code)
            return mcp_detail
        logger.info("[D-TOOLS-002] Verified guide attached: server_code=%s guide_len=%d", server_code, len(guide))
        return {**mcp_detail, "verified_guide": guide}

    async def analyze(self, ctx: DiagnosticContext) -> list[Finding]:
        logger.info("[D-TOOLS-002] analyze start: bot_id=%s entity_id=%s", ctx.bot_id, ctx.entity_id)
        content = await ctx.read_file("TOOLS.md")
        if not content.strip():
            logger.warning("[D-TOOLS-002] TOOLS.md is empty for bot=%s, skipping LLM call", ctx.bot_id)
            return []

        # 获取用户已配置的 MCP 列表
        mcp_list: list[dict] = []
        try:
            mcp_list = ctx.bot_profile.get_activated_mcps(
                entity_id=ctx.entity_id,
                bot_id=ctx.bot_id,
                user_id=ctx.entity_id,
                entity_type=ctx.entity_type,
            )
            logger.info("[D-TOOLS-002] get_activated_mcps success: bot=%s count=%d", ctx.bot_id, len(mcp_list))
        except Exception:
            logger.warning("[D-TOOLS-002] get_activated_mcps failed for bot=%s", ctx.bot_id, exc_info=True)

        # 查询每个 MCP 的描述和工具列表，并附加人工校验的调用规范
        mcp_details: list[dict] = []
        if mcp_list:
            try:
                mcp_center = ctx.mcp_center
                if mcp_center is None:
                    raise RuntimeError("DiagnosticContext.mcp_center not provided")
                for mcp in mcp_list:
                    enriched = self._enrich_mcp_with_detail(mcp, mcp_center)
                    if enriched["server_code"]:
                        mcp_details.append(self._attach_verified_guide(enriched))
            except Exception:
                logger.warning(
                    "[D-TOOLS-002] mcp_center enrichment failed for bot=%s, "
                    "falling back to basic MCP names",
                    ctx.bot_id,
                    exc_info=True,
                )
                # Fallback: use raw mcp list without enrichment
                for mcp in mcp_list:
                    server_code = mcp.get("server_code") or ""
                    name = mcp.get("name") or mcp.get("mcp_server_name") or ""
                    if server_code or name:
                        entry = {
                            "server_code": server_code,
                            "name": name or server_code,
                            "description": mcp.get("description", ""),
                            "tools": [],
                            "verified_guide": None,
                        }
                        if server_code:
                            entry = self._attach_verified_guide(entry)
                        mcp_details.append(entry)
                logger.info(
                    "[D-TOOLS-002] MCP fallback enrichment: bot=%s details=%d",
                    ctx.bot_id, len(mcp_details),
                )

        intro_msg = (
            "请检查下面的 TOOLS.md 是否已经为已配置 MCP 提供了充分的调用规范。"
            "如果缺失或不完整，请输出诊断结论，并优先给出可直接补充到 TOOLS.md 中的"
            "`## 场景与工具映射速查` 和 `##  MCP 调用规范` 草案。\n\n"
            f"--- TOOLS.md MCP 调用规范诊断 ---\n{content}\n"
        )
        if not mcp_details:
            response = await ctx.llm.chat(
                system=self.system_prompt, user=intro_msg + "--- end ---",
                max_tokens=DIAGNOSTIC_MAX_TOKENS,
            )
            logger.info("[D-TOOLS-002] LLM response received: bot=%s response_len=%d", ctx.bot_id, len(response))
        else:
            batches = _pack_mcp_batches(_render_bucketed_blocks(mcp_details))
            if len(batches) == 1:
                # Single batch → prompt identical to the pre-batching path.
                user_msg = intro_msg + _render_mcp_sections(batches[0])
                kb_context = _fetch_kb_quiet(mcp_details, ctx.kb_config, ctx.bot_id)
                logger.info(
                    "[D-TOOLS-002] KB context fetched: bot=%s kb_included=%s",
                    ctx.bot_id, bool(kb_context),
                )
                if kb_context:
                    user_msg += f"\n{kb_context}"
                user_msg += "--- end ---"
                response = await ctx.llm.chat(
                    system=self.system_prompt, user=user_msg, max_tokens=DIAGNOSTIC_MAX_TOKENS,
                )
                logger.info("[D-TOOLS-002] LLM response received: bot=%s response_len=%d", ctx.bot_id, len(response))
            else:
                response = await self._chat_in_batches(ctx, intro_msg, batches)
        findings = self._analyze_response(response, ctx.bot_id)

        # Decide whether this run produced any concrete, patchable call-spec
        # content: a human-verified guide, or at least one tool whose
        # inputSchema we handed the LLM. If not, the finding is purely advisory
        # (the platform must author the specs; the owner can't fix it) — mirror
        # the D-SOUL B-mode treatment (`soul.py:295-297`): drop the auto-fix
        # template and bump the finding to a non-depressing pass so it doesn't
        # keep health_score stuck. The finding still surfaces in the report
        # (message kept) with an explicit "平台补全中" note. LLM-down (LLM01)
        # findings are left alone — that's an operational error, not advisory.
        has_recoverable = any(
            mcp.get("verified_guide")
            or any(
                isinstance(t, dict) and t.get("inputSchema")
                for t in (mcp.get("tools") or [])
            )
            for mcp in mcp_details
        )
        if not has_recoverable:
            for f in findings:
                if f.rule_id == "LLM01":
                    continue
                f.suggested_template_ids = []
                f.score = max(f.score, 85)
                f.result = score_to_result(f.score)  # "pass"
                f.severity = Severity.INFO
                if "平台补全中" not in (f.message or ""):
                    f.message = (f.message or "") + (
                        "\n\n（注：这些 MCP 暂无可自动生成的调用规范，"
                        "已登记，由平台补全中；本项不计入健康分。）"
                    )

        return findings

    async def _chat_in_batches(
        self,
        ctx: DiagnosticContext,
        intro_msg: str,
        batches: list[list[tuple[str, str, dict]]],
    ) -> str:
        """Run one bounded-size LLM call per MCP batch, concurrently, and merge.

        Each batch call carries only its own MCPs' blocks plus the batch note,
        so prompt size is decoupled from the bot's total MCP count. At most
        ``_BATCH_CONCURRENCY`` calls run at once. A batch whose call raises is
        reported as undiagnosed in the merged response instead of failing the
        whole scan.
        """
        total = len(batches)
        sem = asyncio.Semaphore(_BATCH_CONCURRENCY)
        logger.info(
            "[D-TOOLS-002] batched prompt: bot=%s batches=%d max_mcps=%d char_budget=%d concurrency=%d",
            ctx.bot_id, total, _BATCH_MAX_MCPS, _BATCH_CHAR_BUDGET, _BATCH_CONCURRENCY,
        )

        async def run_one(idx: int, batch: list[tuple[str, str, dict]]) -> tuple[str | None, list[str]]:
            async with sem:
                codes = [d.get("server_code", "") for _, _, d in batch]
                note = (
                    f"注意：本次仅诊断已配置 MCP 的一部分（第 {idx + 1}/{total} 批，"
                    f"本批 {len(batch)} 个 MCP）；请只评估本批 MCP 的覆盖与质量，"
                    "草案与评分仅针对本批。\n\n"
                )
                msg = note + intro_msg + _render_mcp_sections(batch)
                kb_context = _fetch_kb_quiet([d for _, _, d in batch], ctx.kb_config, ctx.bot_id)
                if kb_context:
                    msg += f"\n{kb_context}"
                msg += "--- end ---"
                try:
                    resp = await ctx.llm.chat(
                        system=self.system_prompt, user=msg, max_tokens=DIAGNOSTIC_MAX_TOKENS,
                    )
                except Exception:
                    logger.warning(
                        "[D-TOOLS-002] batch %d/%d chat raised: bot=%s",
                        idx + 1, total, ctx.bot_id, exc_info=True,
                    )
                    return None, codes
                logger.info(
                    "[D-TOOLS-002] batch %d/%d LLM response received: bot=%s response_len=%d",
                    idx + 1, total, ctx.bot_id, len(resp or ""),
                )
                return resp, codes

        results = await asyncio.gather(*(run_one(i, b) for i, b in enumerate(batches)))
        return _synthesize_batch_responses(results)
