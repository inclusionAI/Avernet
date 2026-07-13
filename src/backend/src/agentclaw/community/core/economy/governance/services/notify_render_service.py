"""[内核类] 通知渲染服务 — 收口所有"领域模型 → 可投递内容"的纯计算。

类别:**内核**(Kernel)。services 三类职责(编排/内核/能力)中,本服务是内核:
把 governance 领域状态翻译成可投递的通知正文 / TC 卡片 payload / 详情链接。
不碰状态机推进、不碰持久化、不碰投递本身。

依赖边界:
  - 上行(web):无(不经 router 直接调用,由编排服务 scan/record_process 调用)。
  - 下行(repo):**无** ── 只读领域模型属性,不 import `repositories/`、
    不 import `domain/protocols`、不调 `transition_*`。
  - 横向(service):无(底层 builder 函数已内聚到本文件模块级,见下)。

设计说明(领域模型设计方法 — 渲染收口):
  - 渲染逻辑此前散落三处:scan `_render_reminder_md` / `_build_tc_card_payload`、
    record_process `_render_notification_md`,各内联一份"组装字段→调 builder"。
  - 本服务是唯一对外出口,三处编排服务一律改调这里,达成"渲染口径唯一"(spec A4)。
  - **不给 domain 实体挂 render 方法**(spec 约束):实体仅暴露属性,渲染在外部
    服务完成 ── 实体文件零改动。
  - TC 卡片构建失败时 `build_send_payload` 返回 None,调用方据此降级 markdown
    (与原 scan `_build_tc_card_payload` 返回 None 语义一致)。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import base64
import json
import urllib.parse

if TYPE_CHECKING:
    from agentclaw.community.core.economy.governance.domain.notification import (
        GovernanceNotification,
    )
    from agentclaw.community.core.economy.governance.domain.record import (
        GovernanceRecord,
    )
    from agentclaw.community.core.economy.governance.domain.ticket import (
        GovernanceTicket,
    )


@dataclass(frozen=True, slots=True)
class SendPayload:
    """单条通知 TC 卡片投递产物(channel=tc_card,正文已渲染)。

    `build_send_payload` 成功时返回本对象;TC 卡片构建失败返回 None(调用方降级
    markdown)。非 domain 实体,仅 render 模块内部流转用。

    title 不在此产物内 ── 标题取值依 `notify.notify_type`(FIRST_SEND 首通 /
    REMINDER 提醒),是调用方编排逻辑,不归渲染。
    """

    body: str                  # TC 卡片正文(简化 reason)
    deep_link: str             # TC 卡片详情链接
    extra: dict[str, Any]      # TC 卡片 extra(bot_id/card_id/notification_data/...)


class NotifyRenderService:
    """通知渲染内核服务 ── 无状态、无 IO。

    经 DI 注入(`di/modules/economy_governance_module`);构造不依赖 repo /
    notify_sender,仅复用 `notify_builder_service` 模块函数。可单测(Mock 不需要)。
    """

    def render_first_notification_md(
        self,
        record: GovernanceRecord,
        *,
        dt_version: str,
        use_reopen_template: bool = False,
        reopen_ref_time: datetime | None = None,
    ) -> str:
        """渲染离线批首通知 Markdown(收口 record_process `_render_notification_md`)。

        Args:
            record: 上层输入的治理记录领域模型(领域校验后载体,非 DB 行)。
            dt_version: 数据版本日期(如 "20260623")。
            use_reopen_template: True → 走"重新治理"模板(§7.1.4 Step 6)。
            reopen_ref_time: 重开模板里"曾在 X 处理过"的参考时间。

        Returns:
            通知正文 Markdown 字符串。
        """
        if use_reopen_template:
            # "重新治理" template (§7.1.4 Step 6)
            time_str = (
                reopen_ref_time.strftime("%Y-%m-%d %H:%M")
                if reopen_ref_time
                else "之前"
            )
            return (
                f"#### 🔄 重新治理通知 — {record.bot_name or '未知Bot'}\n\n"
                f"该治理项曾在 {time_str} 处理过反馈。"
                f"基于最新数据复核，当前仍需要继续跟进。\n\n"
                f"请参考以下建议处理；如有补充说明，也可以继续反馈。\n\n"
                f"**命中维度**: {record.hit_dimensions or '—'}\n"
                f"**数据日期**: {dt_version}\n"
            )

        # Standard first notification template — use simplified reason builder
        return build_governance_reason(
            bot_name=record.bot_name or "",
            dt_version=dt_version,
            hit_dimensions=record.hit_dimensions,
            governance_max_priority=record.governance_max_priority,
            expected_token_saving=record.expected_token_saving,
            saving_ratio=record.saving_ratio,
            task_summary=record.task_summary,
            notification_structured=record.notification_structured,
        )

    def render_reminder_md(
        self,
        ticket: GovernanceTicket,
        *,
        now: datetime,
    ) -> str:
        """渲染提醒通知 Markdown(收口 scan `_render_reminder_md`)。

        Args:
            ticket: 治理工单领域模型(读取其快照 + 节流元信息)。
            now: cron tick 当前时刻(用于计算 overdue_days)。

        Returns:
            提醒正文 Markdown 字符串。
        """
        days_since = (now - ticket.last_sync_at).days if ticket.last_sync_at else 0
        return build_governance_reason(
            bot_name=ticket.bot_name,
            dt_version=ticket.dt_version,
            hit_dimensions=ticket.triggered_dimensions,
            governance_max_priority=ticket.severity,
            overdue_days=days_since,
        )

    def build_send_payload(
        self,
        notify: GovernanceNotification,
        *,
        user_id: str,
        config: Any,  # EconomyGovernanceConfig
    ) -> SendPayload | None:
        """构建 TC 卡片投递产物(收口 scan `_build_tc_card_payload`)。

        Args:
            notify: 通知领域模型。
            user_id: 收件人 staff_id(用于详情链接 staff_id 参数)。
            config: EconomyGovernanceConfig ── 取 tc_card_id / tc_card_preview_url /
                iframe_callback_url。

        Returns:
            SendPayload(channel=tc_card) 成功;**TC 卡片构建失败返 None** →
            调用方应降级为 markdown 频道(与原 `_build_tc_card_payload` 返回
            None 语义一致)。
        """
        try:
            reason = build_governance_reason(
                notification_structured=notify.notification_structured,
                bot_name=notify.bot_name,
                dt_version=notify.dt_version,
                hit_dimensions=notify.triggered_dimensions,
                governance_max_priority=notify.severity,
                expected_token_saving=notify.estimated_saving_tokens,
                saving_ratio=notify.saving_ratio,
                task_summary=None,
            )

            notification_data = build_card_notification_data(
                notification_structured=notify.notification_structured,
                notification_id=notify.notification_id,
                bot_id=notify.bot_id,
                bot_name=notify.bot_name,
                owner_id=notify.owner_id,
                dt_version=notify.dt_version,
                expected_token_saving=notify.estimated_saving_tokens,
                saving_ratio=notify.saving_ratio,
                governance_max_priority=notify.severity,
            )

            detail_link = build_tc_card_detail_link(
                bot_id=notify.bot_id,
                card_id=config.tc_card_id,
                notification_data=notification_data,
                base_url=config.tc_card_preview_url,
                iframe_callback_url=config.iframe_callback_url,
                staff_id=user_id,
            )

            extra = {
                "bot_id": notify.bot_id,
                "card_id": config.tc_card_id,
                "notification_data": notification_data,
                "out_track_id_prefix": "gov-notify",
            }
            return SendPayload(
                body=reason,
                deep_link=detail_link,
                extra=extra,
            )
        except Exception:
            from agentclaw.community.log import get_logger
            get_logger(__name__).exception(
                "[NotifyRender] TC card build failed for notification_id=%s",
                notify.notification_id,
            )
            return None

# ===========================================================================
# 底层渲染纯计算函数(原 notify_builder_service,内聚进本文件作模块级公开。
# 保原公开名,可直接单测;不再有独立 builder 模块,渲染出口唯一。)
# ===========================================================================


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(val: Any) -> float | None:
    """Safely coerce a value to float; return None on failure."""
    try:
        return float(val)
    except (ValueError, TypeError):
        return None

def _parse_notification_structured(
    raw: str | None,
) -> dict[str, Any] | None:
    """Parse ``notification_structured`` JSON string.

    Returns ``None`` if the input is empty, not valid JSON, or missing
    required keys (``meta``).
    """
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict) or "meta" not in data:
        return None
    return data


# Dimension key → Chinese display name.
# Sourced from governance_dimensions.yaml `display_name` field.
# Keys not in this map are shown as-is (safe fallback).
# Auxiliary metrics (e.g. cron_token_ratio) are intentionally
# excluded — they are internal signals, not user-facing problems.
_DIMENSION_DISPLAY_NAMES: dict[str, str] = {
    "cron_high_freq": "Cron 高频调用",
    "low_efficiency": "低效率",
    "low_utility": "低效用",
    "quality_defect": "质量缺陷",
    "high_error": "高错误率",
    "peak_staggering": "错峰治理",
    "device_duplicate_scheduling": "调度重复",
}

# Auxiliary dimensions that are internal signals, not user-facing
# problems.  These are filtered out from the card shell display.
_AUXILIARY_DIMENSIONS: frozenset[str] = frozenset({
    "cron_token_ratio",  # ratio metric only, not an actionable problem
})


def _format_hit_dimensions(hit_dimensions: Any) -> str:
    """Format hit_dimensions for display with Chinese labels.

    Accepts: comma-separated string, JSON list, or Python list.
    Returns: display string with `` · `` separator.

    Dimension keys are translated to Chinese using
    ``_DIMENSION_DISPLAY_NAMES`` (sourced from the upstream
    ``governance_dimensions.yaml`` ``display_name`` field).
    """
    if isinstance(hit_dimensions, list):
        items = [str(d) for d in hit_dimensions]
    elif isinstance(hit_dimensions, str):
        try:
            parsed = json.loads(hit_dimensions)
            if isinstance(parsed, list):
                items = [str(d) for d in parsed]
            else:
                items = [hit_dimensions]
        except (json.JSONDecodeError, TypeError):
            items = [s.strip() for s in hit_dimensions.split(",") if s.strip()]
    else:
        items = [str(hit_dimensions)]
    # Filter out auxiliary (non-user-facing) dimensions
    filtered = [d for d in items if d not in _AUXILIARY_DIMENSIONS]
    # Translate each key to its Chinese display name
    translated = [_DIMENSION_DISPLAY_NAMES.get(d, d) for d in filtered]
    return " · ".join(translated)


# ---------------------------------------------------------------------------
# TC Card: Reason builder (Markdown for DDRichTextView in card shell)
# ---------------------------------------------------------------------------


def _fmt_tokens(v: Any) -> str:
    """Format token count to human-readable string."""
    n = v if isinstance(v, (int, float)) else 0
    if n >= 1_0000_0000:
        return f"{n / 1_0000_0000:.2f} 亿"
    if n >= 1_0000:
        return f"{n / 1_0000:.0f} 万"
    return f"{n:,.0f}"


def _shorten(text: Any, max_len: int) -> str:
    """Truncate text to *max_len* characters with ellipsis.

    Each field is limited individually so that no single field
    dominates the card shell, and Markdown is never left half-rendered
    by a bulk truncation.
    """
    if text is None:
        return ""
    s = str(text).strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "…"


def _extract_primary_suggestion(
    suggestions: list[dict[str, Any]] | None,
) -> str:
    """Return the first suggestion as a single-line summary.

    Only the top suggestion is shown in the card shell — the full
    list belongs in the detail iframe.
    """
    if not suggestions:
        return ""
    s = suggestions[0]
    title = s.get("title") or s.get("action") or ""
    desc = s.get("description") or s.get("what_to_change") or ""
    if title and desc:
        return _shorten(f"{title}：{desc}", 120)
    return _shorten(title or desc, 120)


# Per-field character budgets for the card shell reason.
_SHORTEN_TITLE = 40
_SHORTEN_DIMENSIONS = 60
_SHORTEN_PROBLEM = 80
_MAX_REASON_LENGTH = 800


def build_governance_reason(
    *,
    notification_structured: str | dict[str, Any] | None = None,
    bot_name: str | None = None,
    dt_version: str | None = None,
    hit_dimensions: str | None = None,
    governance_max_priority: str | None = None,
    expected_token_saving: int | None = None,
    saving_ratio: float | None = None,
    task_summary: str | None = None,
    overdue_days: int = 0,
) -> str:
    """Build Markdown reason content for the TC card shell.

    Design goal: let the responsible person understand **who, what,
    how much, and where to act** within 5 seconds.  The full analysis
    lives in the detail iframe — the shell only carries the essentials
    to drive action, not to reproduce the report.

    Layout (≤ 4 blocks, target 300–500 chars):

    1. Overdue warning (reminders only)
    2. Bot name + hit dimensions
    3. Key metrics: daily cost / potential saving
    4. CTA: click detail to act (suggestions live in the detail iframe)

    Each field is individually shortened (``_shorten``) to its
    character budget.  The final result is capped at 1200 chars
    (down from 2000) — bulk truncation is a last resort.

    Args:
        notification_structured: JSON string or dict from ODPS pipeline.
        bot_name: Fallback bot name.
        dt_version: Data version date (e.g. ``"20260623"``), displayed
            as the sampling date so the user knows the data is historical.
        hit_dimensions: Fallback hit dimensions.
        governance_max_priority: Fallback priority.
        expected_token_saving: Fallback token saving.
        saving_ratio: Fallback saving ratio.
        task_summary: Fallback summary.
        overdue_days: If > 0, prepend overdue warning prefix.

    Returns:
        Markdown string suitable for the card shell reason variable.
    """
    structured = _parse_notification_structured(notification_structured)
    meta = structured.get("meta", {}) if structured else {}

    # ---- Resolve fields (structured-first, then fallbacks) ----

    # Bot display name
    title = meta.get("botName") or bot_name or ""
    if not title and structured:
        title = structured.get("title") or meta.get("title") or ""
    if not title:
        title = "Bot"

    # Dimensions
    if structured and meta.get("hit_dimensions"):
        dimensions = _format_hit_dimensions(meta.get("hit_dimensions"))
    else:
        dimensions = _format_hit_dimensions(hit_dimensions) if hit_dimensions else ""

    # Metrics
    daily_tokens = meta.get("daily_tokens") or meta.get("dailyTokenUsage")

    # Problem summary — one-line digest for the card shell
    if structured:
        raw_problem = structured.get("problem_summary") or task_summary or ""
    else:
        raw_problem = task_summary or ""

    # ---- Render ----
    lines: list[str] = []

    # 1. Overdue warning (reminders)
    if overdue_days > 0:
        lines.append(f"⚠️ 此通知已超期 {overdue_days} 天未处理")
        lines.append("")

    # 2. Bot name (bold, own line) + core statement
    bot_display = _shorten(title, _SHORTEN_TITLE)
    lines.append(f"**「{bot_display}」**")
    lines.append("近期存在可优化的 Token 消耗")

    # 3. Hit dimensions (if any remain after auxiliary filtering)
    if dimensions:
        lines.append("")
        lines.append(f"**命中维度**：{_shorten(dimensions, _SHORTEN_DIMENSIONS)}")

    # 4. Key metric (bold label, prominent)
    if daily_tokens:
        lines.append("")
        if isinstance(daily_tokens, (int, float)):
            daily_text = _fmt_tokens(daily_tokens)
        else:
            daily_text = str(daily_tokens).strip()
        lines.append(f"**日均消耗**：{daily_text} Token")

    # 5. Data sampling date
    if dt_version:
        # Format: "20260623" → "2026-06-23"
        formatted_date = f"{dt_version[:4]}-{dt_version[4:6]}-{dt_version[6:8]}" if len(dt_version) == 8 else dt_version
        lines.append("")
        lines.append(f"**采样日期**：{formatted_date}")

    # 6. Problem summary — one line, makes the card feel substantive
    if raw_problem:
        lines.append("")
        lines.append(f"**主要问题**：{_shorten(raw_problem, _SHORTEN_PROBLEM)}")

    result = "\n".join(lines)

    if len(result) > _MAX_REASON_LENGTH:
        result = result[: _MAX_REASON_LENGTH - 1].rstrip() + "…"

    return result


# ---------------------------------------------------------------------------
# TC Card: notification data contract builder (shared)
# ---------------------------------------------------------------------------


def build_card_notification_data(
    *,
    notification_structured: str | dict[str, Any] | None,
    notification_id: str,
    bot_id: str | None = None,
    bot_name: str | None = None,
    owner_id: str | None = None,
    dt_version: str | None = None,
    expected_token_saving: int | None = None,
    saving_ratio: float | None = None,
    governance_max_priority: str | None = None,
) -> dict[str, Any]:
    """Build the standard card-data contract from ``notification_structured``.

    Single source of truth for the card-data shape consumed by the TC card
    detailLink and the teamclaw preview iframe (React ``LLMComponent_v2.jsx``).
    See ``05-card-llm-prompt.md`` §一 for the contract spec.

    The output preserves the ODPS nested format verbatim AND derives flat
    compat keys (``botId`` / ``severity`` / ``optimizationSuggestions`` …) so
    both nested (v3) and flat (v2) card templates can read their own keys.

    Args:
        notification_structured: JSON string or dict from the ODPS pipeline.
        notification_id: Notification unique ID (emitted as both
            ``notification_id`` and ``noticeId`` for ``extractNotice`` compat).
        bot_id / bot_name / owner_id / dt_version: Fallback identity fields
            used when the structured payload omits them.
        expected_token_saving / saving_ratio / governance_max_priority:
            Fallback metric fields.

    Returns:
        A dict ready to be wrapped in ``{"data": <this>}`` and base64-encoded
        into the detailLink URL. Never raises — malformed input degrades to
        the minimal fallback shape.
    """
    nid = notification_id
    owner_default = owner_id or ""

    parsed: dict[str, Any] | None = None
    if notification_structured:
        try:
            candidate = (
                json.loads(notification_structured)
                if isinstance(notification_structured, str)
                else notification_structured
            )
            if isinstance(candidate, dict):
                parsed = candidate
        except (json.JSONDecodeError, TypeError):
            parsed = None

    if parsed is not None:
        meta = parsed.get("meta", {})
        raw_items = (
            parsed.get("action_items")
            or parsed.get("optimizationSuggestions")
            or []
        )
        flat_suggestions = [
            {
                "title": item.get("action") or item.get("title") or "",
                "description": item.get("what_to_change")
                or item.get("description")
                or "",
            }
            for item in raw_items
        ]
        daily_raw = (
            meta.get("daily_tokens_raw")
            or meta.get("dailyTokenUsage")
            or 0
        )
        meta_saving_ratio = meta.get("saving_ratio")
        try:
            meta_ratio_val = float(meta_saving_ratio) if meta_saving_ratio else None
        except (ValueError, TypeError):
            meta_ratio_val = None
        opt_rate = (
            meta.get("optimization_rate")
            or meta.get("optimizationRate")
            or (f"{meta_ratio_val:.1%}" if meta_ratio_val else "")
        )
        result: dict[str, Any] = {
            "notification_id": nid,
            "noticeId": nid,
            "schema_version": parsed.get("schema_version", "v1"),
            "title": parsed.get("title") or bot_name or "成本优化通知",
            "meta": meta,
            "action_items": raw_items,
            "problem_summary": parsed.get("problem_summary") or "",
            "has_skill_section": parsed.get("has_skill_section", False),
            "disclaimer": parsed.get("disclaimer") or "",
            "degraded": parsed.get("degraded", False),
            "severity": (
                meta.get("severity")
                or meta.get("optimization_summary", "")
                or governance_max_priority
                or ""
            ),
            "botName": meta.get("botName") or bot_name or "",
            "botId": bot_id or "",
            "owner": meta.get("owner", owner_default),
            "organization": meta.get("organization") or meta.get("department", ""),
            "statDate": dt_version or "",
            "dailyTokenUsage": daily_raw,
            "optimizationPotential": expected_token_saving or 0,
            "optimizationRate": opt_rate,
            "summary": parsed.get("problem_summary") or "",
            "optimizationSuggestions": flat_suggestions,
            "feedback": {
                "isAdopted": None,
                "action": None,
                "schedule": None,
                "notes": "",
            },
        }
        for key, value in parsed.items():
            if key not in result:
                result[key] = value
        return result

    return {
        "notification_id": nid,
        "noticeId": nid,
        "title": bot_name or "成本优化通知",
        "meta": {},
        "action_items": [],
        "problem_summary": "",
        "has_skill_section": False,
        "disclaimer": "",
        "degraded": False,
        "severity": governance_max_priority or "",
        "botName": bot_name or "",
        "botId": bot_id or "",
        "owner": owner_default,
        "organization": "",
        "statDate": dt_version or "",
        "dailyTokenUsage": 0,
        "optimizationPotential": expected_token_saving or 0,
        "optimizationRate": (
            f"{float(saving_ratio):.1%}" if saving_ratio and _safe_float(saving_ratio) is not None
            else (str(saving_ratio) if saving_ratio else "")
        ),
        "summary": "",
        "optimizationSuggestions": [],
        "feedback": {"isAdopted": None, "action": None, "schedule": None, "notes": ""},
    }


# ---------------------------------------------------------------------------
# TC Card: detailLink builder (3-layer nested DingTalk deep link)
# ---------------------------------------------------------------------------


def build_tc_card_detail_link(
    *,
    bot_id: str,
    card_id: str,
    notification_data: dict[str, Any],
    card_params: dict[str, Any] | None = None,
    base_url: str = "",
    iframe_callback_url: str = "",
    staff_id: str = "",
) -> str:
    """Build a 3-layer nested DingTalk deep link for TC card detail.

    Structure::

        detailLink = open_platform_link?pcLink=...&mobileLink=...
        pcLink     = open_side_popup_wnd?url=<encoded_inner>&ddtab=true
        inner      = preview?...&data=<base64>&params=<base64>
                     &callbackUrl=<url>&staffId=<id>

    The inner URL opens in the DingTalk sidebar (PC) or browser (mobile),
    loading the teamclaw preview page with the React component
    that renders full notification detail + feedback form.

    Args:
        bot_id: Bot ID.
        card_id: Aix card component ID (from config).
        notification_data: Full structured notification data dict,
            base64-encoded into the URL ``data`` parameter.
        card_params: Card params dict (default: ``{"type": "custom",
            "botId": bot_id}``), base64-encoded into ``params``.
        base_url: teamclaw preview page URL.
        iframe_callback_url: Backend callback URL for the card React
            component's ``fetch POST``.  Encoded into the inner URL's
            ``callbackUrl`` query param (read by ``getCallbackUrl()``).
            **Only used in detailLink URL — never placed into the
            DingTalk API payload.**  Empty string = not encoded.
        staff_id: Recipient staff ID, encoded into the inner URL's
            ``staffId`` query param (read by ``getStaffId()``).
            Empty string = not encoded.

    Returns:
        Complete DingTalk deep link string.
    """
    if card_params is None:
        card_params = {"type": "custom", "botId": bot_id}

    # Inject callbackUrl + staffId into the card data payload so the React
    # component can read them directly (notice.callbackUrl / notice.staffId)
    # without relying on regex over the double-encoded detailLink URL.
    # The query-param encoding below is kept as a backward-compat fallback.
    card_data = dict(notification_data) if isinstance(notification_data, dict) else {}
    if iframe_callback_url:
        card_data.setdefault("callbackUrl", iframe_callback_url)
    if staff_id:
        card_data.setdefault("staffId", staff_id)

    # base64-encode data (UTF-8)
    data_json = json.dumps({"data": card_data}, ensure_ascii=False, default=str)
    data_b64 = base64.b64encode(data_json.encode("utf-8")).decode("ascii")

    params_json = json.dumps(card_params, ensure_ascii=False)
    params_b64 = base64.b64encode(params_json.encode("utf-8")).decode("ascii")

    # inner URL: teamclaw preview page + all params
    inner_url = (
        f"{base_url}?skipBrain=true"
        f"&type=custom"
        f"&botId={urllib.parse.quote(bot_id)}"
        f"&cardId={urllib.parse.quote(card_id)}"
        f"&data={urllib.parse.quote(data_b64)}"
        f"&params={urllib.parse.quote(params_b64)}"
    )

    # iframe callback URL + staff ID: encoded into inner URL query params
    # for the React component to discover via getCallbackUrl() / getStaffId()
    if iframe_callback_url:
        inner_url += f"&callbackUrl={urllib.parse.quote(iframe_callback_url, safe='')}"
    if staff_id:
        inner_url += f"&staffId={urllib.parse.quote(staff_id, safe='')}"

    # PC deep link: open in DingTalk sidebar
    pc_link_raw = (
        f"dingtalk://dingtalkclient/action/open_side_popup_wnd?url="
        f"{urllib.parse.quote(inner_url + '&ddtab=true')}"
    )

    # Mobile deep link: direct browser open
    mobile_link = inner_url

    # Final detailLink: platform link wrapping PC + mobile
    detail_link = (
        f"dingtalk://dingtalkclient/action/open_platform_link?"
        f"pcLink={urllib.parse.quote(pc_link_raw)}"
        f"&mobileLink={urllib.parse.quote(mobile_link)}"
    )

    return detail_link
