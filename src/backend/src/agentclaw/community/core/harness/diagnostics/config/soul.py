"""D-SOUL-001 — SOUL.md 人格描述诊断."""
import json
import logging
from typing import Any

import requests

from agentclaw.community.core.harness.diagnostics.base import Diagnostic, DiagnosticContext
from agentclaw.community.core.harness.models import Finding
from agentclaw.community.core.harness.services.llm import DIAGNOSTIC_MAX_TOKENS
logger = logging.getLogger(__name__)

# ── BCSFuse 配置 ──────────────────────────────────────────────────
# The BCSFuse base URL is deployment config (the ``bcsfuse`` yaml block, read
# via BcsFuseConfig and threaded through DiagnosticContext); the neutral shipped
# code embeds no endpoint. Empty = this diagnostic's profile fetch is skipped
# (feature-off).
_BCSFUSE_PROFILE_TIMEOUT = 10
_BCSFUSE_HIGH_QUALITY_THRESHOLD = 0.7


def _fetch_profiles(worker_id: str, base_url: str) -> dict[str, Any] | None:
    """Fetch profiles list from BCSFuse.

    Returns the full response dict on success, None on failure (including when
    no BCSFuse ``base_url`` is configured — the diagnostic simply skips).
    Response structure:
      { "items": [...], "total": N, "active_profile_id": "default", ... }
    """
    if not base_url:
        logger.info("[D-SOUL-001] BCSFuse base_url not set; skipping profile fetch")
        return None
    url = f"{base_url}/v1/workers/{worker_id}/profiles"
    try:
        resp = requests.get(url, timeout=_BCSFUSE_PROFILE_TIMEOUT)
        result = resp.json()
        if resp.ok:
            return result
        logger.warning(
            "[D-SOUL-001] BCSFuse profiles returned error: "
            "status=%s body=%s worker_id=%s",
            resp.status_code,
            json.dumps(result, ensure_ascii=False)[:500],
            worker_id,
        )
        return None
    except Exception:
        logger.warning(
            "[D-SOUL-001] BCSFuse profiles request failed: worker_id=%s",
            worker_id,
            exc_info=True,
        )
        return None


def _extract_active_contents(
    profiles_resp: dict[str, Any],
) -> dict[str, Any] | None:
    """Extract the active profile's contents from BCSFuse profiles response.

    Returns a dict with keys: display_name, contents, quality_score, quality_issues.
    Returns None if no active profile found.
    """
    items = profiles_resp.get("items", [])
    if not items:
        return None

    active_id = profiles_resp.get("active_profile_id", "")
    for item in items:
        if item.get("profile_id") == active_id:
            return {
                "display_name": item.get("display_name", ""),
                "contents": item.get("contents", {}),
                "quality_score": item.get("quality_score"),
                "quality_issues": item.get("quality_issues", []),
            }

    first = items[0]
    return {
        "display_name": first.get("display_name", ""),
        "contents": first.get("contents", {}),
        "quality_score": first.get("quality_score"),
        "quality_issues": first.get("quality_issues", []),
    }


def _build_profile_summary(active: dict[str, Any]) -> str:
    """Build a human-readable profile summary from active profile contents."""
    contents = active.get("contents", {})
    parts: list[str] = []

    display_name = active.get("display_name", "")
    if display_name:
        parts.append(f"Bot 名称: {display_name}")

    profile_text = contents.get("profile", "")
    if profile_text:
        parts.append(f"画像描述:\n{profile_text}")

    short_profile = contents.get("short_profile", "")
    if short_profile:
        parts.append(f"简短画像: {short_profile}")

    capabilities = contents.get("capabilities", [])
    if capabilities:
        parts.append(f"能力标签: {', '.join(capabilities)}")

    quality_score = active.get("quality_score")
    if quality_score is not None:
        parts.append(f"画像质量分: {quality_score}")

    quality_issues = active.get("quality_issues", [])
    if quality_issues:
        parts.append(f"画像质量问题: {'; '.join(quality_issues)}")

    return "\n".join(parts)


class SoulPersonaDiagnostic(Diagnostic):
    id = "D-SOUL-001"
    name = "SOUL.md 人格描述诊断"
    severity = "info"
    file_type = "SOUL.md"
    suggested_template_ids: list[int] = [3]

    system_prompt = """\
    你是一个 Bot 配置文件诊断与改写专家。请结合 SOUL.md 内容与 Bot 画像信息，判断这份 SOUL.md 是否为“当前这个 Bot”做了专属人格定制，并在必要时直接产出修改后的 SOUL.md 建议稿。

    你的目标分为两步：
    第一步：诊断当前 SOUL.md 是否存在问题；
    第二步：如果问题明显且 Bot 画像信息足够完整，则直接生成一版更适合当前 Bot 的 SOUL.md 草案。

    【诊断目标】
    请判断以下几点：
    1. SOUL.md 是否为空、过短、仅有寒暄，或缺乏有效内容；
    2. SOUL.md 是否定义了人格/性格特征、行为原则、沟通风格、语气态度；
    3. SOUL.md 是否体现了当前 Bot 的专属身份、职责、能力重点或使用场景；
    4. SOUL.md 是否更像一个平台默认模板、共享模板或可复用于多个 Bot 的通用模板；
    5. 只有在 SOUL.md 明确写出了与 Bot 画像相反的身份、职责、能力边界或使用场景时，才判定为“与画像冲突”。

    【判定原则：请严格遵守】
    一、以下情况通常属于“未定制”或“不够贴合”，不应判定为冲突：
    - 使用了较通用的人格词，如“专业、友好、直接、严谨”
    - 文本质量一般，但没有写出与画像相反的身份设定
    - 没有覆盖画像中的全部能力标签
    - 更像默认模板、通用研发助手模板、共享人格模板
    - 已体现技术助手/研发助手方向，但缺少当前 Bot 的专属特征
    - 只是描述偏抽象、偏通用、信息不够具体

    二、如果 SOUL.md 是明显的通用个人助手/默认助手模板，并且包含少量私人事务、消息、日历、邮件、群聊、外部发言等泛化场景表述：
    - 应优先判定为“通用模板问题”或“缺少专属定制”
    - 可以补充说明“与当前 Bot 画像存在场景偏移”
    - 除非文本明确将 Bot 定义为另一类角色，并围绕该角色职责展开，否则不要上升为“明显冲突”或“严重冲突”

    三、只有满足以下情况之一，才可以判定为“明显冲突”：
    - SOUL.md 明确把 Bot 写成另一类角色，如生活助理、情感陪伴、客服、销售、运营，而画像是研发/技术助手
    - SOUL.md 的核心职责与画像明显相反，如重点强调生活安排、社交沟通、邮件日程管理、个人事务处理，而画像是代码开发、问题排查、技术调研
    - SOUL.md 规定的目标用户、能力边界、使用场景与画像明显不一致
    - SOUL.md 中有明确文字证据，足以证明它描述的是另一种 Bot，而不是当前这个 Bot

    四、当 SOUL.md 与画像大体方向一致时，应优先按以下方式判断：
    - 如果内容清晰且能体现当前 Bot 的专属特征，可判定为“无问题”
    - 如果内容不差，但更像通用模板，缺少对当前 Bot 的专属体现，应判定为“缺少专属定制”或“人格描述过于通用”
    - 不要把“没有充分结合画像”误判成“与画像冲突”

    【关于默认模板】
    SOUL.md 有可能来自 openclaw 或平台提供的默认模板。默认模板不等于错误。
    如果一份 SOUL.md 与 Bot 画像方向基本一致，或者只是包含少量默认个人助手语境，但整体没有明确把 Bot 定义成另一类角色，那么通常应判断为“通用模板/定制不足”或“存在场景偏移”，而不是“冲突”。

    【关于 Bot 画像信息】
    - Bot 画像信息是参考基线，用来判断 SOUL.md 是否贴合当前 Bot 的实际定位
    - 如果画像质量分低于 0.5，说明画像本身也可能不完善。此时可以建议完善画像，但不要仅凭低质量画像就武断认定 SOUL.md 有冲突
    - 如果 SOUL.md 已明确体现研发、代码、排障、平台操作、安全边界等特征，而画像也是技术方向，通常视为基本匹配
    - 如果画像质量分较高（例如 >= 0.7），且画像描述、能力标签较完整，那么在发现“通用模板问题”“缺少专属定制”或“存在场景偏移”时，请直接生成一版“修改后的 SOUL.md 建议稿”

    【请重点检查】
    - 是否有人格/性格特征
    - 是否有语气、风格、态度、沟通方式
    - 是否给出可执行的行为指导，而不是只写空泛形容词
    - 是否体现当前 Bot 的名称、职责、擅长任务、典型工作方式或场景边界
    - 是否能看出这是“这个 Bot”的 SOUL，而不是“任何 Bot 都能用”的模板

    【输出规则】
    请根据情况选择一种输出形式：

    1. 如果没有发现问题，请输出"无问题"

    2. 如果发现问题，且画像信息不足以支持完整改写，请输出：
    第一行：5-7个字的简短总结
    后续行：详细说明问题，并给出中文修复建议
    建议应优先围绕“如何结合当前 Bot 画像做专属定制”展开
    如果判断为“未定制/过于通用”，请明确写出这是“通用模板问题”或“缺少专属定制”
    如果存在少量私人助手语境，请表述为“场景偏移”，不要轻易写成“冲突”
    只有证据充分时，才可以写“与画像冲突”
    最后一行输出评分标记：[SCORE:XX]

    3. 如果发现问题，且画像质量较高、信息较完整，足以支持改写，请输出：
    第一行：5-7个字的简短总结
    后续先简要说明问题
    然后新增一个小节标题：建议版 SOUL.md
    在该小节下，直接给出一份完整的、可替换的 SOUL.md 建议稿，使用 Markdown 格式
    该建议稿必须：
    - 与当前 Bot 画像一致
    - 体现当前 Bot 的名称、职责、能力重点和典型场景
    - 保留清晰的人格、边界、沟通风格和行为原则
    - 尽量沿用优秀通用模板中的结构优点，但要改写成“当前这个 Bot”的版本
    最后一行输出评分标记：[SCORE:XX]

    【改写要求】
    当你生成“建议版 SOUL.md”时：
    - 不要只给提纲，必须给完整正文
    - 使用自然、可执行、清晰的表达
    - 不要空泛堆砌形容词
    - 不要臆造画像中完全没有依据的特殊权限或职责
    - 如果画像信息不足某些细节，可以采取稳妥表述，不要过度发挥
    - 如果原 SOUL.md 中已有高质量内容且与画像一致，可以保留其结构和优点，但要完成针对当前 Bot 的定制化改写
    - 如果原 SOUL.md 来自默认模板，可保留其组织方式，但必须去除与当前 Bot 无关的默认个人助理语境

    【评分细则】
    满分100，从100开始扣分：
    - SOUL.md 完全为空、极短、只有寒暄或无实际指导意义：-60
    - 缺乏人格/性格特征定义：-25
    - 缺乏语气/风格/态度等沟通特征：-20
    - 明显像默认模板/通用模板，缺少当前 Bot 的专属定制：-20
    - 存在与当前 Bot 无关的默认个人助理语境或场景偏移：-10
    - 描述过于笼统，缺少具体行为指导：-10
    - 与 Bot 画像存在明确冲突：-15
    - 与 AGENTS.md 角色定义存在明确矛盾：-10
    最低10分

    【额外要求】
    - 不要臆造 SOUL.md 中不存在的内容
    - 不要把“信息不足”“不够贴合”说成“严重冲突”
    - 不要因为画像质量低，就直接判定 SOUL.md 错误
    - 如果 SOUL.md 已明显体现研发助手、代码开发、问题排查、生产安全、平台操作等内容，应优先视为“基本匹配”，除非存在明确反证
    """

    async def analyze(self, ctx: DiagnosticContext) -> list[Finding]:
        content = await ctx.read_file("SOUL.md")
        if not content.strip():
            logger.warning(
                "[D-SOUL-001] SOUL.md is empty for bot=%s, skipping LLM call",
                ctx.bot_id,
            )
            return []

        user_msg = (
            "请重点判断这份 SOUL.md 是否为当前 Bot 做了专属定制。"
            "注意：该文件很可能是 openclaw 或平台提供的默认 SOUL 模板。"
            "如果它主要表现为通用助手模板，即使包含少量邮件、消息、日历、群聊、私人事务、外部发言等默认个人助手语境，也应优先判断为“缺少专属定制”或“通用模板问题”，必要时补充说明“存在场景偏移”；"
            "只有当文本明确把 Bot 定义成与画像相反的另一类角色，并以该角色职责为核心展开时，才判定为“冲突”。"
        )

        worker_id = f"{ctx.bot_id}:{ctx.entity_id}"
        profiles_resp = _fetch_profiles(worker_id, ctx.bcsfuse_base_url)
        active: dict[str, Any] | None = None
        quality_score: float | None = None
        should_generate_full_rewrite = False
        if profiles_resp:
            active = _extract_active_contents(profiles_resp)
            if active:
                raw_score = active.get("quality_score")
                if isinstance(raw_score, (int, float)):
                    quality_score = float(raw_score)
                    if quality_score >= _BCSFUSE_HIGH_QUALITY_THRESHOLD:
                        should_generate_full_rewrite = True
        if should_generate_full_rewrite:
            user_msg += (
                " 当前 Bot 画像质量较高、信息较完整。"
                "如果你判断 SOUL.md 存在通用模板问题、缺少专属定制或存在场景偏移，请不要只给抽象建议，"
                "而是务必直接输出一版完整的“建议版 SOUL.md”，可直接供用户参考或替换。"
            )
        else:
            user_msg += (
                " 如果当前 Bot 画像信息不足，或画像质量不高，请优先给出诊断与修改建议，不要勉强生成完整改写稿。"
            )
        user_msg += f"\n\n--- SOUL.md 人格描述诊断 ---\n{content}\n"
        if active:
            profile_summary = _build_profile_summary(active)
            if profile_summary:
                user_msg += f"\n--- Bot 画像信息 ---\n{profile_summary}\n"
        user_msg += "--- end ---"

        response = await ctx.llm.chat(system=self.system_prompt, user=user_msg, max_tokens=DIAGNOSTIC_MAX_TOKENS)
        return self._analyze_response(response, ctx.bot_id)
