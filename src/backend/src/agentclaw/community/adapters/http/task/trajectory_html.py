"""HTML 渲染 ``GET /tasks/trajectory?display=html`` 的轨迹页(REQ-P2 可视化)。

纯函数:``TaskTrajectoryDTO`` → 自包含 HTML 字符串(内联 CSS,无外部依赖,任意浏览器直出)。
默认响应仍是 JSON envelope(``Envelope[TaskTrajectoryDTO]``);仅当请求带 ``display=html`` 时
本模块被路由调用并返回 ``HTMLResponse``(FastAPI 对 ``Response`` 返回值跳过 ``response_model``
序列化,故与 ``response_model=Envelope[TaskTrajectoryDTO]`` 不冲突)。

所有动态文本经 ``html.escape``(防 XSS:``action_input``/``error_msg`` 等来自外部 bot/请求)。
``analysis`` 是 ``TrajectoryAnalysis`` JSON 字符串(执行者多源,DTO 层保持 string 透出),这里
解析出 boost_reason / failure_reason / analysis_output 等做人类可读呈现,解析失败则降级显示原文。
``ext_info`` 不在 DTO(REQ-1),故页面不展示候选/JOIN 丢因等附加素材(仅 action_input/error)。
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentclaw.community.adapters.http.task.schemas import TaskTrajectoryDTO


# action_type → 主题色(左侧色条 + 徽章底色)。7 个轨迹动作类型各一色。
_ACTION_THEME: dict[str, str] = {
    "submit": "#2563eb",      # 蓝
    "plan": "#7c3aed",        # 紫
    "dispatch": "#0891b2",    # 青
    "execute": "#16a34a",     # 绿
    "verify": "#0d9488",      # 蓝绿
    "reset": "#ea580c",       # 橙
    "transition": "#64748b",  # 灰
}

# 被视为"失败/异常"的 action_result —— 徽章标红(仅视觉,不影响语义)。
_FAILURE_RESULTS: frozenset[str] = frozenset(
    {"failed", "miss", "dispatch_exception", "no_result", "form_group_failed",
     "start_run_failed", "sla_timeout", "pending_dispatch_stuck", "call_fail",
     "parse_fail", "exec_failed_retry", "accept_fail"}
)


def _fmt_time(ms: int | None) -> str:
    """ms epoch → ``YYYY-MM-DD HH:MM:SS UTC``(None → ``-``)。"""
    if not ms:
        return "-"
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S") + " UTC"
    except (OverflowError, OSError, ValueError):
        return f"{ms}ms"


def _parse_analysis(analysis: str | None) -> dict | None:
    """安全解析 ``TrajectoryAnalysis`` JSON 字符串;失败 → None(页面降级显示原文)。"""
    if not analysis:
        return None
    try:
        data = json.loads(analysis)
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


def _badge_class(action_type: str, action_result: str) -> str:
    theme = _ACTION_THEME.get(action_type, "#475569")
    is_fail = action_result in _FAILURE_RESULTS
    # 内联底色(主题色低饱和) + 失败时红字
    style = f"background:{theme}1f;color:{theme};border:1px solid {theme}55;"
    cls = "badge" + (" badge-fail" if is_fail else "")
    return f'<span class="{cls}" style="{style}">{html.escape(action_type)}</span>'


def _result_chip(action_result: str, error_type: str | None) -> str:
    is_fail = (action_result in _FAILURE_RESULTS) or bool(error_type)
    cls = "chip" + (" chip-err" if is_fail else " chip-ok")
    return f'<span class="{cls}">{html.escape(action_result)}</span>'


def _render_event(ev: "object") -> str:
    """渲染单条事件卡片(`ev` 是 TrajectoryEventDTO;以属性访问,避免 hard 依赖)。"""
    action_type = str(getattr(ev, "action_type", "") or "")
    action_result = str(getattr(ev, "action_result", "") or "")
    node_id = str(getattr(ev, "node_id", "") or "")
    attempt = getattr(ev, "attempt", 0)
    gmt_create = getattr(ev, "gmt_create", None)
    status_from = getattr(ev, "status_from", None)
    status_to = getattr(ev, "status_to", None)
    error_type = getattr(ev, "error_type", None)
    error_msg = getattr(ev, "error_msg", None)
    action_input = getattr(ev, "action_input", None)

    theme = _ACTION_THEME.get(action_type, "#475569")
    bar = f' style="border-left:4px solid {theme};"'

    parts: list[str] = [f'<div class="event"{bar}>']

    # 头部:徽章 + 结果 chip + 节点 + 重试序号 + 时间
    parts.append('<div class="ev-head">')
    parts.append(_badge_class(action_type, action_result))
    parts.append(_result_chip(action_result, error_type))
    if node_id:
        parts.append(f'<span class="node" title="node_id">{html.escape(node_id)}</span>')
    if attempt:
        parts.append(f'<span class="attempt">attempt #{html.escape(str(attempt))}</span>')
    parts.append(f'<span class="time">{html.escape(_fmt_time(gmt_create))}</span>')
    parts.append("</div>")

    # 状态迁移
    if status_from or status_to:
        frm = html.escape(str(status_from)) if status_from else "∅"
        to = html.escape(str(status_to)) if status_to else "∅"
        parts.append(f'<div class="ev-status">{frm} → {to}</div>')

    # 错误块
    if error_type or error_msg:
        parts.append('<div class="ev-error">')
        if error_type:
            parts.append(f'<span class="err-type">{html.escape(str(error_type))}</span>')
        if error_msg:
            parts.append(f'<span class="err-msg">{html.escape(str(error_msg))}</span>')
        parts.append("</div>")

    # action_input(可折叠;防长文本撑爆页面)
    if action_input:
        preview = str(action_input)
        if len(preview) > 80:
            preview = preview[:80] + "…"
        parts.append('<details class="ev-input">')
        parts.append(f'<summary>action_input · <code>{html.escape(preview)}</code></summary>')
        parts.append(f'<pre>{html.escape(str(action_input))}</pre>')
        parts.append("</details>")

    parts.append("</div>")
    return "".join(parts)


def _render_analysis(analysis_raw: str | None, *, do_analysis: bool) -> str:
    """渲染总体分析块(boost/failure_reason 等)。解析失败或无分析 → 降级提示。"""
    parsed = _parse_analysis(analysis_raw)
    if parsed is None:
        if do_analysis:
            note = "本次分析未产出(do_analysis=true 但 analysis 为空——bot 可能未配置或失败,见 503/504)。"
        else:
            note = "未分析(do_analysis=false;调 ?do_analysis=true 触发 bot 总体分析)。"
        return f'<section class="analysis analysis-empty"><h2>总体分析</h2><p>{html.escape(note)}</p></section>'

    def _field(label: str, key: str) -> str:
        val = parsed.get(key)
        if not val:
            return ""
        return (
            f'<div class="a-row"><span class="a-label">{html.escape(label)}</span>'
            f'<span class="a-value">{html.escape(str(val))}</span></div>'
        )

    body = "".join(
        [
            _field("分析执行者类型", "analysis_type"),
            _field("执行者", "analysis_executor"),
            _field("推进理由 (boost_reason)", "boost_reason"),
            _field("失败根因 (failure_reason)", "failure_reason"),
            _field("结论汇总", "analysis_output"),
            _field("分析时间", "gmt_create"),
        ]
    )
    # analysis_input 是喂给分析器的结构化摘要,折叠
    a_input = parsed.get("analysis_input")
    if a_input:
        preview = str(a_input)[:120] + ("…" if len(str(a_input)) > 120 else "")
        body += (
            f'<details class="a-input"><summary>分析输入摘要 · <code>{html.escape(preview)}</code></summary>'
            f'<pre>{html.escape(str(a_input))}</pre></details>'
        )
    return f'<section class="analysis"><h2>总体分析</h2>{body}</section>'


def render_trajectory_html(dto: "TaskTrajectoryDTO", *, do_analysis: bool = False) -> str:
    """``TaskTrajectoryDTO`` → 自包含 HTML 页面(``display=html`` 时由路由返回)。

    纯函数 + 全转义;不触碰 ``response_model``(路由返回 ``HTMLResponse`` 时 FastAPI 跳过模型序列化)。
    """
    task_id = str(getattr(dto, "task_id", "") or "")
    timeline = list(getattr(dto, "timeline", []) or [])
    analysis_raw = getattr(dto, "analysis", None)
    gmt_create = getattr(dto, "gmt_create", None)
    gmt_modified = getattr(dto, "gmt_modified", None)

    events_html = "".join(_render_event(ev) for ev in timeline) if timeline else (
        '<p class="empty">时间线为空(该任务尚无轨迹事件,或 task_id 不存在)。</p>'
    )

    head = f"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>任务轨迹 · {html.escape(task_id)}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "PingFang SC", "Segoe UI", Roboto, system-ui, sans-serif;
         margin: 0; background: #f8fafc; color: #0f172a; line-height: 1.5; }}
  header {{ background: #fff; border-bottom: 1px solid #e2e8f0; padding: 18px 24px; }}
  header h1 {{ margin: 0 0 6px; font-size: 20px; }}
  header .meta {{ color: #64748b; font-size: 13px; }}
  header .meta code {{ background: #f1f5f9; padding: 1px 6px; border-radius: 4px; font-size: 12px; }}
  header .pill {{ display: inline-block; background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe;
                 border-radius: 999px; padding: 1px 9px; font-size: 12px; margin-left: 8px; }}
  .wrap {{ max-width: 1000px; margin: 0 auto; padding: 20px 24px 60px; }}
  .analysis {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 16px 18px; margin-bottom: 20px; }}
  .analysis h2 {{ margin: 0 0 12px; font-size: 15px; color: #334155; }}
  .analysis-empty p {{ color: #64748b; margin: 0; }}
  .a-row {{ display: flex; gap: 12px; padding: 6px 0; border-bottom: 1px dashed #e2e8f0; font-size: 13px; }}
  .a-row:last-of-type {{ border-bottom: none; }}
  .a-label {{ flex: 0 0 180px; color: #64748b; }}
  .a-value {{ flex: 1; word-break: break-word; }}
  .a-value:empty::after {{ content: "—"; color: #cbd5e1; }}
  details.a-input {{ margin-top: 10px; font-size: 13px; }}
  details.a-input pre {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px;
                         padding: 10px; white-space: pre-wrap; word-break: break-word; font-size: 12px; }}
  .timeline h2 {{ font-size: 15px; color: #334155; margin: 8px 0 12px; }}
  .event {{ background: #fff; border: 1px solid #e2e8f0; border-left-width: 4px;
            border-radius: 8px; padding: 12px 14px; margin-bottom: 10px; }}
  .ev-head {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px; font-size: 13px; }}
  .badge {{ font-size: 12px; font-weight: 600; padding: 2px 9px; border-radius: 999px; }}
  .badge-fail {{ text-decoration: none; }}
  .chip {{ font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 4px; }}
  .chip-ok {{ background: #f0fdf4; color: #166534; border: 1px solid #bbf7d0; }}
  .chip-err {{ background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }}
  .node {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: #475569; font-size: 12px; }}
  .attempt, .time {{ color: #94a3b8; font-size: 12px; }}
  .time {{ margin-left: auto; }}
  .ev-status {{ margin-top: 7px; font-size: 12px; color: #475569; font-family: ui-monospace, monospace; }}
  .ev-error {{ margin-top: 8px; padding: 7px 10px; background: #fef2f2; border: 1px solid #fecaca;
              border-radius: 6px; font-size: 12px; }}
  .err-type {{ font-weight: 600; color: #991b1b; margin-right: 8px; }}
  .err-msg {{ color: #7f1d1d; word-break: break-word; }}
  details.ev-input {{ margin-top: 8px; font-size: 12px; color: #475569; }}
  details.ev-input summary {{ cursor: pointer; }}
  details.ev-input summary code {{ font-family: ui-monospace, monospace; color: #64748b; }}
  details.ev-input pre {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px;
                          padding: 8px; white-space: pre-wrap; word-break: break-word; margin: 6px 0 0; }}
  .empty {{ color: #94a3b8; font-style: italic; }}
  footer {{ max-width: 1000px; margin: 0 auto; padding: 0 24px 40px; color: #94a3b8; font-size: 12px; }}
  footer a {{ color: #2563eb; text-decoration: none; }}
  footer a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<header>
  <h1>任务轨迹</h1>
  <div class="meta">
    task_id: <code>{html.escape(task_id)}</code>
    <span class="pill">事件 {len(timeline)}</span>
    <span class="pill">do_analysis={ "true" if do_analysis else "false" }</span>
    <span class="pill">组装于 {html.escape(_fmt_time(gmt_create))}</span>
    <span class="pill">回填于 {html.escape(_fmt_time(gmt_modified))}</span>
  </div>
</header>
<div class="wrap">
  {_render_analysis(analysis_raw, do_analysis=do_analysis)}
  <section class="timeline"><h2>时间线</h2>{events_html}</section>
</div>
<footer>
  默认 JSON: 去掉 <code>?display=html</code> &nbsp;·&nbsp;
  触发分析: 加 <code>&amp;do_analysis=true</code>
</footer>
</body>
</html>"""
    return head
