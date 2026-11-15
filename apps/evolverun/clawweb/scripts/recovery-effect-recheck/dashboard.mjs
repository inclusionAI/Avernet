const escape = value => String(value ?? "—").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const states = { ready: "排队", starting: "派发确认中", running: "补验中", done: "已结束", failed: "执行异常", excluded: "已排除", unresolved: "待核实" };
const effects = { recovered: "已恢复", not_recovered: "确认未恢复", unknown: "仍未知", superseded: "已由在线流程接管" };
const callbacks = { pending: "等待回传", accepted: "诊断已接收", exhausted: "回传重试已用尽" };
const reasons = { online_resume_available: "在线仍可续接", online_task: "在线流程处理中", execution_pending: "尚有在途执行或工具",
  effect_already_known: "效果已有结论", not_a_live_intervention: "未实际干预", source_unavailable: "读取失败，待重扫",
  wait_history_unverified: "缺少等待时间证据", eligible: "已确认干预", intervention_needs_evidence: "待从原会话补证",
  result_delivery_only: "仅补传现有结论", result_delivery_pending: "已有结论待回传", original_target_changed: "原运行目标已变化" };
const cell = value => `<td>${escape(value)}</td>`;
export function render(scans, counts, rows, page) {
  const total = counts.reduce((sum, entry) => sum + entry.count, 0);
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="5"><title>自愈效果补验</title><style>
*{box-sizing:border-box}body{margin:0;color:#182535;background:#f4f7fb;font:14px system-ui,sans-serif}main{max-width:1250px;margin:38px auto;padding:0 22px}
h1{font-size:30px;margin:0}h2{font-size:17px}p{color:#516174}ul{display:flex;flex-wrap:wrap;gap:12px;list-style:none;padding:0}li,section{border:1px solid #d9e3ee;background:white;border-radius:10px;padding:12px 16px}
strong{font-size:21px;margin-left:8px}section{margin:16px 0;overflow:auto}table{width:100%;border-collapse:collapse;text-align:left;white-space:nowrap}
td,th{padding:10px 8px;border-bottom:1px solid #e6edf4}th{color:#50647c}a{color:#14529c}nav{display:flex;gap:15px;padding:10px 0}
</style></head><body><main><h1>自愈效果补验</h1><p>每 5 秒刷新。诊断已接收表示结果已回传；DI 按原创建日显示，需等待后续数据同步。</p>
<ul>${Object.entries(states).map(([key, label]) => `<li>${label}<strong>${counts.find(item => item.state === key)?.count ?? 0}</strong></li>`).join("")}</ul>
<section><h2>扫描范围</h2>${scans.length ? scans.map(item => `<p>${escape(item.id)} · ${escape(({ scanning:"扫描中", finished:"扫描结束", paused:"已暂停" })[item.state])} · 已扫描 ${item.count} 条</p>`).join("") : "<p>暂无扫描记录</p>"}</section>
<section><h2>任务明细 · ${total} 条</h2><table><thead><tr><th>任务 ID</th><th>原创建时间</th><th>控制面</th><th>原状态</th><th>补验状态</th><th>原因</th><th>效果</th><th>回传</th></tr></thead><tbody>
${rows.map(row => `<tr>${[row.id,row.created,row.plane,row.source_status,states[row.state],reasons[row.reason] ?? row.reason,
  effects[row.effect] ?? row.effect,callbacks[row.callback]].map(cell).join("")}</tr>`).join("") || '<tr><td colspan="8">暂无任务</td></tr>'}</tbody></table>
<nav>${page > 1 ? `<a href="?page=${page - 1}">上一页</a>` : ""} 第 ${page} 页 ${page * 100 < total ? `<a href="?page=${page + 1}">下一页</a>` : ""}</nav></section></main></body></html>`;
}
