"""Simple HTML/SVG exporter for a DAG plan and its full execution report."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from ._dag import DAGNode, DAGPlan, NodeStatus

__all__ = ["export_plan", "export_report", "render_plan", "render_report"]

_STATUS_COLORS = {
    NodeStatus.PENDING: "#9aa4b2",
    NodeStatus.RUNNING: "#f5a623",
    NodeStatus.SUCCEEDED: "#4caf50",
    NodeStatus.FAILED: "#e53935",
    NodeStatus.SKIPPED: "#757575",
}

_NODE_W = 180
_NODE_H = 56
_LAYER_GAP = 120


def render_plan(plan: DAGPlan) -> str:
    return _render(plan)


def export_plan(plan: DAGPlan, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_render(plan), encoding="utf-8")
    return target


def render_report(plan: DAGPlan, goal: str, final_output: Any, succeeded: bool) -> str:
    return _render_report(plan, goal, final_output, succeeded)


def export_report(
    plan: DAGPlan, goal: str, final_output: Any, succeeded: bool, path: str | Path
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_render_report(plan, goal, final_output, succeeded), encoding="utf-8")
    return target


def render_report_md(plan: DAGPlan, goal: str, final_output: Any, succeeded: bool) -> str:
    result_text = (
        final_output
        if isinstance(final_output, str)
        else json.dumps(final_output, ensure_ascii=False, indent=2)
    )
    lines = [
        "# Agent Compute — Task Report",
        "",
        f"**Status:** {'completed' if succeeded else 'failed'}",
        f"**Goal:** {goal}",
        "",
        "## Final Result",
        "",
        "````markdown",
        result_text or "(no output)",
        "````",
        "",
        "## DAG Nodes",
        "",
        "| Node | Agent | Plan Round | Status | Started | Finished |",
        "|------|-------|------------|--------|---------|----------|",
    ]
    for node in plan.nodes.values():
        lines.append(
            f"| {node.id} | {node.agent} | {node.plan_round} | {node.status.value} | "
            f"{node.started_at or ''} | {node.finished_at or ''} |"
        )
    lines += ["", "### Per-node input / output", ""]
    for node in plan.nodes.values():
        input_text = json.dumps(node.input, ensure_ascii=False, indent=2)
        output_text = (
            node.result
            if isinstance(node.result, str)
            else json.dumps(node.result, ensure_ascii=False, indent=2)
        )
        lines += [
            f"#### {node.id} ({node.agent}) — {node.status.value}",
            "",
            "**Input:**",
            "````json",
            input_text,
            "````",
            "**Output:**",
            "````json",
            output_text or "(no output)",
            "````",
            "",
            f"**Error:** {node.error or '—'}",
            "",
        ]
    if plan.extension_history:
        lines += [
            "",
            "## Plan Extension History",
            "",
            f"_The plan was extended **{len(plan.extension_history)}** times "
            "during execution by the dynamic replanner._",
            "",
        ]
        for i, record in enumerate(plan.extension_history, start=1):
            wave = record.get("wave_index", "—")
            applied_at = record.get("applied_at", "—")
            rationale = record.get("rationale", "")
            halt_reason = record.get("halt_reason") or "—"
            nodes_added = record.get("nodes_added", [])
            edges_added = record.get("edges_added", [])
            lines += [
                f"### Extension #{i} — wave {wave} (halt: {halt_reason})",
                "",
                f"- **Applied at:** {applied_at}",
                f"- **Rationale:** {rationale or '—'}",
                f"- **Nodes added ({len(nodes_added)}):**",
            ]
            for node_dict in nodes_added:
                node_id = node_dict.get("id", "?")
                agent = node_dict.get("agent", "?")
                node_goal = node_dict.get("input", {}).get("goal", "")
                lines.append(f"  - `{node_id}` [{agent}] goal={node_goal!r}")
            if edges_added:
                lines.append(f"- **Edges added ({len(edges_added)}):**")
                for src, dst in edges_added:
                    lines.append(f"  - `{src}` → `{dst}`")
            lines.append("")
    lines += ["", "## Planner Prompt", "", "```text", plan.prompt or "(no prompt)", "```"]
    return "\n".join(lines)


def _layers(plan: DAGPlan) -> dict[str, int]:
    layers: dict[str, int] = {}
    for node_id in plan.topological_order():
        deps = plan.dependencies_of(node_id)
        layer = 0 if not deps else max(layers[d] for d in deps) + 1
        layers[node_id] = layer
    return layers


def _render(plan: DAGPlan) -> str:
    layers = _layers(plan)
    max_layer = max(layers.values(), default=0)
    width = max(800, (max_layer + 1) * (_NODE_W + _LAYER_GAP) + 40)
    height = max(200, len(plan.nodes) * _NODE_H + 60)

    per_layer: dict[int, list[str]] = {}
    for nid, layer in layers.items():
        per_layer.setdefault(layer, []).append(nid)

    positions: dict[str, tuple[float, float]] = {}
    node_svg = []
    for layer, nids in per_layer.items():
        x = 40 + layer * (_NODE_W + _LAYER_GAP)
        total_h = len(nids) * _NODE_H
        y0 = (height - total_h) / 2
        for i, nid in enumerate(nids):
            y = y0 + i * _NODE_H + _NODE_H / 2
            positions[nid] = (x + _NODE_W / 2, y)
            node_svg.append(_node_svg(plan.nodes[nid], x, y - _NODE_H / 2))

    edge_svg = []
    for src, dst in plan.edges:
        sx, sy = positions[src]
        dx, dy = positions[dst]
        edge_svg.append(
            f'<path d="M {sx:.1f} {sy:.1f} C {(sx + 120):.1f} {sy:.1f}, '
            f'{(dx - 120):.1f} {dy:.1f}, {dx:.1f} {dy:.1f}" '
            f'fill="none" stroke="#5b6b7b" stroke-width="2" marker-end="url(#arrow)"/>'
        )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>Agent Compute DAG</title>
<style>
body {{ margin:0; background:#10151c; color:#e6edf3; font-family:system-ui,sans-serif; }}
header {{ padding:16px 24px; border-bottom:1px solid #242c38; }}
h1 {{ font-size:18px; margin:0; }}
.legend {{ display:flex; gap:16px; padding:12px 24px; font-size:13px; }}
.legend span {{ display:inline-flex; align-items:center; gap:6px; }}
.dot {{ width:10px; height:10px; border-radius:2px; }}
svg {{ display:block; }}
</style>
</head>
<body>
<header><h1>Agent Compute — DAG Plan</h1></header>
<div class="legend">
  {"".join(f'<span><i class="dot" style="background:{c}"></i>{s.value.lower()}</span>' for s, c in _STATUS_COLORS.items())}
</div>
<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3"
            orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L0,6 L9,3 z" fill="#5b6b7b"/>
    </marker>
  </defs>
  {"".join(edge_svg)}
  {"".join(node_svg)}
</svg>
</body>
</html>"""


def _node_svg(node: DAGNode, x: float, y: float) -> str:
    color = _STATUS_COLORS[node.status]
    label = html.escape(f"{node.id}  [{node.agent}]")
    parts = [f"P{node.plan_round}"]
    if node.wave is not None:
        parts.append(f"W{node.wave}")
    parts.append(node.status.value)
    title = html.escape(" · ".join(parts))
    return (
        f"<g>"
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{_NODE_W}" height="{_NODE_H}" '
        f'rx="8" fill="#1c252f" stroke="{color}" stroke-width="2"/>'
        f'<text x="{(x + _NODE_W / 2):.1f}" y="{(y + 22):.1f}" text-anchor="middle" '
        f'fill="#e6edf3" font-size="13">{label}</text>'
        f'<text x="{(x + _NODE_W / 2):.1f}" y="{(y + 42):.1f}" text-anchor="middle" '
        f'fill="{color}" font-size="11">{title}</text>'
        f"</g>"
    )


def _render_report(plan: DAGPlan, goal: str, final_output: Any, succeeded: bool) -> str:
    stat = "completed" if succeeded else "failed"
    status_color = "#4caf50" if succeeded else "#e53935"
    agents = sorted({n.agent for n in plan.nodes.values()})
    agents_text = html.escape(", ".join(agents))
    rows = "".join(_report_row(n) for n in plan.nodes.values())
    final_is_str = isinstance(final_output, str)
    final_is_md_dict = (
        not final_is_str
        and isinstance(final_output, dict)
        and all(isinstance(v, str) for v in final_output.values())
    )
    plan_json_raw = plan.to_json(indent=2)
    plan_json_escaped = html.escape(plan_json_raw)
    extension_section = _render_extension_section(plan)
    if final_is_str:
        final_raw = final_output
        final_json_str = json.dumps(final_raw, ensure_ascii=False)
        final_section = '<div class="markdown-body" id="final-output"></div>'
    elif final_is_md_dict:
        final_json_str = json.dumps(final_output, ensure_ascii=False)
        items = []
        for nid, val in final_output.items():
            val_escaped = html.escape(val) if val else "(empty)"
            items.append(
                f'<div class="final-node-block">'
                f'<div class="final-node-label">Node {html.escape(str(nid))}</div>'
                f'<div class="markdown-body final-node-md" data-raw="{val_escaped}"></div>'
                f'</div>'
            )
        final_section = "".join(items)
    else:
        final_raw = json.dumps(final_output, ensure_ascii=False, indent=2)
        final_escaped = html.escape(final_raw)
        final_json_str = json.dumps(final_raw, ensure_ascii=False)
        final_section = (
            '<div class="detail-wrap">'
            '<button class="copy-btn" onclick="_copyCode(this)">copy</button>'
            '<pre class="detail"><code class="language-json" id="final-json">'
            f"{final_escaped}</code></pre></div>"
        )
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>Agent Compute — Task Report</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css"/>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<style>body {{ margin:0; background:#10151c; color:#e6edf3; font-family:system-ui,sans-serif; }}
header {{ padding:16px 24px; border-bottom:1px solid #242c38; }}
h1 {{ font-size:18px; margin:0; }}
h2 {{ font-size:14px; margin:16px 24px 8px; }}
.meta {{ padding:12px 24px; font-size:13px; }}
.meta .req {{ color:#79b8ff; }}
.meta .goal {{ color:#7ee787; }}
.legend {{ display:flex; gap:16px; padding:12px 24px; font-size:13px; }}
.legend span {{ display:inline-flex; align-items:center; gap:6px; }}
.dot {{ width:10px; height:10px; border-radius:2px; }}
svg {{ display:block; margin:0 24px; }}
table {{ margin:8px 24px; border-collapse:collapse; font-size:12px; width:calc(100% - 48px); }}
th, td {{ border:1px solid #242c38; padding:6px 10px; text-align:left; vertical-align:top; }}
th {{ background:#1c252f; }}
.detail-wrap {{ margin:8px 24px; position:relative; }}
pre.detail {{ margin:0; padding:12px 44px 12px 12px; background:#1c252f; border:1px solid #242c38; border-radius:6px; font-size:12px; overflow-x:auto; white-space:pre-wrap; }}
.markdown-body {{ margin:8px 24px; padding:16px 20px; background:#1c252f; border:1px solid #242c38; border-radius:6px; font-size:13px; line-height:1.6; overflow-x:auto; }}
.markdown-body h1,.markdown-body h2,.markdown-body h3 {{ margin:16px 0 8px; color:#e6edf3; }}
.markdown-body h1 {{ font-size:20px; border-bottom:1px solid #242c38; padding-bottom:6px; }}
.markdown-body h2 {{ font-size:16px; }}
.markdown-body h3 {{ font-size:14px; }}
.markdown-body p {{ margin:8px 0; }}
.markdown-body ul,.markdown-body ol {{ margin:8px 0; padding-left:24px; }}
.markdown-body li {{ margin:4px 0; }}
.markdown-body code {{ background:#0d1117; padding:2px 6px; border-radius:3px; font-size:12px; }}
.markdown-body pre {{ background:#0d1117; padding:12px; border-radius:6px; overflow-x:auto; margin:8px 0; }}
.markdown-body pre code {{ background:none; padding:0; font-size:12px; }}
.markdown-body table {{ margin:8px 0; font-size:12px; width:100%; }}
.markdown-body th,.markdown-body td {{ padding:6px 10px; }}
.markdown-body blockquote {{ border-left:3px solid #30363d; margin:8px 0; padding:4px 12px; color:#9aa4b2; }}
.markdown-body a {{ color:#79b8ff; }}
td.input, td.result {{ max-width:280px; word-break:break-word; }}
.ext-card {{ margin:8px 24px; padding:10px 14px; background:#1c252f; border:1px solid #242c38; border-radius:6px; }}
.ext-card .h {{ font-size:13px; color:#79b8ff; margin-bottom:4px; }}
.ext-card .r {{ font-size:12px; color:#9aa4b2; }}
.ext-card ul {{ margin:4px 0 0 18px; padding:0; font-size:12px; }}
.copy-btn {{ position:absolute; top:8px; right:8px; padding:4px 10px; background:#30363d; color:#e6edf3; border:1px solid #484f58; border-radius:4px; font-size:11px; cursor:pointer; opacity:0.7; transition:opacity .15s; z-index:1; }}
.copy-btn:hover {{ opacity:1; background:#444c56; }}
.copy-btn.copied {{ background:#238636; border-color:#238636; opacity:1; }}
.final-node-block {{ margin:8px 24px; }}
.final-node-label {{ font-size:12px; color:#79b8ff; margin-bottom:4px; font-weight:600; }}
.final-node-md {{ margin:0 0 16px 0; }}
</style>
</head>
<body>
<header><h1>Agent Compute — Task Report</h1></header>
<h2>Request</h2>
<div class="meta">
  <div><span style="color:{status_color}">Status: {stat}</span></div>
  <div class="goal">Goal: {html.escape(goal)}</div>
  <div class="req">Agents: {agents_text}</div>
</div>
<div class="legend">
  {"".join(f'<span><i class="dot" style="background:{c}"></i>{s.value.lower()}</span>' for s, c in _STATUS_COLORS.items())}
</div>
<h2>DAG Plan</h2>
{_render(plan)}
<h2>Node Detail</h2>
<table>
  <thead><tr><th>Node</th><th>Agent</th><th>Plan Round</th><th>Status</th><th>Input</th><th>Output</th><th>Error</th><th>Started</th><th>Finished</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
{extension_section}
<h2>Final Result</h2>
{final_section}
<h2>Plan (JSON)</h2>
<div class="detail-wrap"><button class="copy-btn" onclick="_copyCode(this)">copy</button><pre class="detail"><code class="language-json" id="plan-json">{plan_json_escaped}</code></pre></div>
<script>
const _finalMode = {"str" if final_is_str else ("md_dict" if final_is_md_dict else "json")};
const _finalData = {final_json_str};
marked.setOptions({{ highlight: function(code, lang) {{ return hljs.highlightAuto(code).value; }} }});
if (_finalMode === "str") {{
  document.getElementById('final-output').innerHTML = marked.parse(_finalData);
}} else if (_finalMode === "md_dict") {{
  document.querySelectorAll('.final-node-md').forEach(el => {{
    const raw = el.getAttribute('data-raw');
    el.innerHTML = marked.parse(raw);
  }});
}}
document.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
document.querySelectorAll('.markdown-body pre').forEach(pre => {{
  if (!pre.querySelector('code')) {{
    const code = document.createElement('code');
    code.textContent = pre.textContent;
    pre.innerHTML = '';
    pre.appendChild(code);
    try {{ hljs.highlightElement(code); }} catch(e) {{}}
  }}
  const wrap = pre.parentElement;
  if (wrap && !wrap.querySelector('.copy-btn')) {{
    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.textContent = 'copy';
    btn.onclick = function() {{ _copyPre(this, pre); }};
    pre.parentElement.style.position = 'relative';
    pre.parentElement.appendChild(btn);
  }}
}});
function _copyCode(btn) {{
  const code = btn.parentElement.querySelector('code');
  if (!code) return;
  navigator.clipboard.writeText(code.textContent).then(() => {{
    btn.textContent='copied'; btn.classList.add('copied');
    setTimeout(() => {{ btn.textContent='copy'; btn.classList.remove('copied'); }},1500);
  }});
}}
function _copyPre(btn, pre) {{
  const code = pre.querySelector('code') || pre;
  navigator.clipboard.writeText(code.textContent).then(() => {{
    btn.textContent='copied'; btn.classList.add('copied');
    setTimeout(() => {{ btn.textContent='copy'; btn.classList.remove('copied'); }},1500);
  }});
}}
</script>
</body>
</html>"""


def _render_extension_section(plan: DAGPlan) -> str:
    if not plan.extension_history:
        return ""
    cards: list[str] = [
        '<h2>Plan Extension History</h2>',
        f'<div class="meta" style="color:#9aa4b2">The plan was extended '
        f"<strong>{len(plan.extension_history)}</strong> time(s) by the dynamic "
        "replanner during execution.</div>",
    ]
    for i, record in enumerate(plan.extension_history, start=1):
        wave = record.get("wave_index", "—")
        applied_at = html.escape(str(record.get("applied_at", "—")))
        rationale = html.escape(record.get("rationale", "") or "")
        halt_reason = html.escape(str(record.get("halt_reason") or "—"))
        nodes_added = record.get("nodes_added", [])
        edges_added = record.get("edges_added", [])
        node_items = "".join(
            f"<li><code>{html.escape(str(nd.get('id', '?')))}</code> "
            f"[{html.escape(str(nd.get('agent', '?')))}] goal="
            f"{html.escape(str(nd.get('input', {}).get('goal', '')))}</li>"
            for nd in nodes_added
        )
        edge_items = "".join(
            f"<li><code>{html.escape(str(src))}</code> → "
            f"<code>{html.escape(str(dst))}</code></li>"
            for src, dst in edges_added
        )
        cards.append(
            f'<div class="ext-card">'
            f'<div class="h">Extension #{i} — wave {wave} · halt: {halt_reason}</div>'
            f'<div class="r"><strong>Applied at:</strong> {applied_at}</div>'
            f'<div class="r"><strong>Rationale:</strong> {rationale or "—"}</div>'
            f'<div class="r"><strong>Nodes added ({len(nodes_added)}):</strong></div>'
            f'<ul>{node_items or "<li>—</li>"}</ul>'
            + (
                f'<div class="r"><strong>Edges added ({len(edges_added)}):</strong></div>'
                f'<ul>{edge_items}</ul>'
                if edges_added
                else ""
            )
            + "</div>"
        )
    return "\n".join(cards)


def _report_row(node: DAGNode) -> str:
    color = _STATUS_COLORS[node.status]
    input_text = html.escape(json.dumps(node.input, ensure_ascii=False))
    result_text = node.result
    result_text = (
        result_text if isinstance(result_text, str) else json.dumps(result_text, ensure_ascii=False)
    )
    result_text = html.escape(result_text or "")[:500]
    return (
        f"<tr>"
        f"<td>{html.escape(node.id)}</td>"
        f"<td>{html.escape(node.agent)}</td>"
        f'<td style="text-align:center">{node.plan_round}</td>'
        f'<td style="color:{color}">{node.status.value}</td>'
        f'<td class="input">{input_text}</td>'
        f'<td class="result">{result_text}</td>'
        f"<td>{html.escape(node.error or '')}</td>"
        f"<td>{html.escape(node.started_at or '')}</td>"
        f"<td>{html.escape(node.finished_at or '')}</td>"
        f"</tr>"
    )
