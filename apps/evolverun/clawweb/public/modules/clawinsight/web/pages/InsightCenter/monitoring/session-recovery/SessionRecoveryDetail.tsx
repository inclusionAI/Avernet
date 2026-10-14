import { useEffect, useState } from 'react';
import { sessionRecoveryApi } from '../../../../api/session-recovery';
import type { SessionRecoveryTask } from '../../../../types/session-recovery';
import { monitoringErrorText } from '../../../../api/monitoring';
import './session-recovery.css';
const statuses = { running: '执行中', waiting_owner: '等待方案确认或配合', waiting_effect: '等待效果', completed: '已完成', failed: '失败' };
const effects = { recovered: '已恢复', not_recovered: '未恢复', unknown: '无法判断', not_intervened: '未实际干预' };
const actors = { service: '自愈服务', agent: '自愈 Agent', owner: 'Owner' };
const time = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'medium' });
export default function SessionRecoveryDetail({ taskId, onBack }: { taskId: string; onBack: () => void }) {
  const [loaded, setLoaded] = useState<{ taskId: string; task: SessionRecoveryTask } | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [annotationLayer, setAnnotationLayer] = useState<'delivery' | 'response' | 'effect'>('effect');
  const [annotationCorrect, setAnnotationCorrect] = useState('true');
  const [annotationReason, setAnnotationReason] = useState('');
  const [annotationEvidence, setAnnotationEvidence] = useState<string[]>([]);
  const [rawEvidence, setRawEvidence] = useState<Record<string, unknown>>({});
  const [ownerFeedback, setOwnerFeedback] = useState('');
  const [cooperationDone, setCooperationDone] = useState(false);
  const task = loaded?.taskId === taskId ? loaded.task : null;
  useEffect(() => {
    let active = true;
    let controller: AbortController | undefined;
    const load = async () => {
      if (document.visibilityState === 'hidden') return;
      controller?.abort(); controller = new AbortController();
      const signal = controller.signal;
      try {
        const value = await sessionRecoveryApi.detail(taskId, signal);
        if (active && !signal.aborted) { setLoaded({ taskId, task: value }); setError(''); }
      } catch (failure) { if (active && !signal.aborted) setError(monitoringErrorText(failure)); }
    };
    void load();
    const timer = setInterval(() => void load(), 20000);
    const visible = () => { if (document.visibilityState === 'visible') void load(); };
    document.addEventListener('visibilitychange', visible);
    return () => { active = false; clearInterval(timer); controller?.abort(); document.removeEventListener('visibilitychange', visible); };
  }, [taskId, revision]);
  useEffect(() => { setCooperationDone(false); setOwnerFeedback(''); }, [taskId, task?.plan?.digest]);
  useEffect(() => { setAnnotationReason(''); setAnnotationEvidence([]); setRawEvidence({}); }, [taskId]);
  const decide = async (approved: boolean) => {
    if (!task?.plan || busy) return;
    setBusy(true);
    try { await sessionRecoveryApi.decide(taskId, task.plan.digest, approved, cooperationDone, ownerFeedback); setRevision(value => value + 1); }
    catch (failure) { setError(monitoringErrorText(failure)); }
    finally { setBusy(false); }
  };
  const loadEvidence = async (id: string) => {
    try { const value = await sessionRecoveryApi.toolCall(taskId, id); setRawEvidence(previous => ({ ...previous, [id]: value })); }
    catch (failure) { setError(monitoringErrorText(failure)); }
  };
  const annotate = async () => {
    if (!task || !annotationReason.trim() || busy) return;
    setBusy(true);
    try {
      await sessionRecoveryApi.annotate(taskId, { layer: annotationLayer, correct: annotationCorrect === 'unknown' ? null : annotationCorrect === 'true', reason: annotationReason, evidence_ids: annotationEvidence });
      setAnnotationReason(''); setAnnotationEvidence([]); setRevision(value => value + 1);
    } catch (failure) { setError(monitoringErrorText(failure)); }
    finally { setBusy(false); }
  };
  return <main className="session-recovery-detail">
    <header><button type="button" onClick={onBack}>返回监控</button><h2>自愈任务</h2><code>{taskId}</code></header>
    {error && <p role="alert">{error}<button type="button" onClick={() => setRevision(value => value + 1)}>重新加载</button></p>}
    {!task ? <p role="status">正在读取任务…</p> : <>
      <section><h3>{statuses[task.status]}</h3><p>目标 Bot：{task.request.target.bot_id}</p><p>{task.request.diagnosis}</p>
        {task.request.error_location && <p>错误位置：{task.request.error_location}</p>}
        <p>阶段：{({ assess: 'Agent 判断干预方式', observe: '观察指令干预', plan: '分析并形成方案', apply: '执行完整方案', verify: '验证原任务恢复' })[task.phase]} · 权限：{({ debug: 'Debug（不实际干预）', approval: '方案审批', automatic: '全自动' })[task.level]}</p>
        <p>效果：{effects[task.judgment?.effect ?? 'unknown']}</p><p>{task.judgment?.summary}</p><p>触达：{task.judgment?.delivery === "confirmed" ? "已确认" : "尚未确认"} · 响应：{({ acted: "已采取行动", replied: "有回复", none: "未发现响应", unknown: "无法判断" })[task.judgment?.response ?? "unknown"]}</p></section>
      {task.plan && <section><h3>完整修复方案</h3><p>{task.plan.summary}</p><p>调整范围：{task.plan.scope}</p>
        <ol>{task.plan.actions.map(action => <li key={action.actionId}><strong>{action.summary}</strong><p>风险：{action.risk}</p>
          <details><summary>查看操作细节</summary>
            <p>验证：{action.verification}</p><p>回退：{action.rollback || "未提供"}</p>
            {action.operation && <pre>{JSON.stringify(action.operation, null, 2)}</pre>}
            {action.engineOperation && <><p>参考接口请求</p><pre>{JSON.stringify(action.engineOperation, null, 2)}</pre></>}
            {action.command && <><p>参考命令</p><pre>{action.command}</pre></>}
          </details></li>)}</ol>
        <details><summary>查看方案依据与验收细节</summary><dl>
          <dt>依据</dt><dd>{task.plan.basis}</dd><dt>验证方法</dt><dd>{task.plan.verification}</dd>
        </dl></details>
        {task.plan.owner_cooperation.length > 0 && <div><h4>需要你配合</h4><ul>{task.plan.owner_cooperation.map((item, index) => <li key={index}>{item.request}<p>依据：{item.basis}</p></li>)}</ul></div>}
        {task.status === 'waiting_owner' && <div className="recovery-decision"><label>补充信息或处理说明<textarea maxLength={4000} value={ownerFeedback} onChange={event => setOwnerFeedback(event.target.value)} /></label>
          {task.plan.owner_cooperation.length > 0 && <label><input type="checkbox" checked={cooperationDone} onChange={event => setCooperationDone(event.target.checked)} />我已处理方案中的配合事项</label>}
          <button type="button" disabled={busy || (task.plan.owner_cooperation.length > 0 && !cooperationDone)} onClick={() => void decide(true)}>确认完整方案</button>
          <button type="button" disabled={busy} onClick={() => void decide(false)}>拒绝方案</button></div>}
      </section>}
      <section><h3>完整任务时间线</h3><ol className="recovery-timeline">{task.timeline.map(event => <li key={event.event_id}><time>{time.format(event.at_ms)}</time><span>{actors[event.actor]}</span><strong>{event.summary}</strong><details><summary>查看原文与依据</summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details></li>)}</ol></section>
      <section><h3>故障与恢复证据</h3>{task.evidence.length ? task.evidence.map(evidence => <article key={evidence.evidence_id}><h4>{evidence.evidence_id}</h4><p>{evidence.source} · {evidence.location}</p><p>工具调用：{evidence.tool_call_id}</p><pre>{evidence.excerpt}</pre><details onToggle={event => { if (event.currentTarget.open && !rawEvidence[evidence.tool_call_id]) void loadEvidence(evidence.tool_call_id); }}><summary>查看完整操作原文与回执</summary><pre>{rawEvidence[evidence.tool_call_id] ? JSON.stringify(rawEvidence[evidence.tool_call_id], null, 2) : '正在读取…'}</pre></details></article>) : <p>尚未取得可核验的证据。</p>}</section>
      <section><h3>人工复核</h3><p>标注会与 Agent 的原判断一并保留，不覆盖原结论。</p>
        <label>判断层次<select value={annotationLayer} onChange={event => setAnnotationLayer(event.target.value as typeof annotationLayer)}><option value="delivery">触达</option><option value="response">响应</option><option value="effect">效果</option></select></label>
        <label>是否正确<select value={annotationCorrect} onChange={event => setAnnotationCorrect(event.target.value)}><option value="true">正确</option><option value="false">错误</option><option value="unknown">无法判断</option></select></label>
        <label>理由<textarea value={annotationReason} maxLength={4000} onChange={event => setAnnotationReason(event.target.value)} /></label>
        <fieldset><legend>引用证据</legend>{task.evidence.map(item => <label key={item.evidence_id}><input type="checkbox" checked={annotationEvidence.includes(item.evidence_id)} onChange={event => setAnnotationEvidence(current => event.target.checked ? [...current, item.evidence_id] : current.filter(id => id !== item.evidence_id))} />{item.evidence_id}</label>)}</fieldset>
        <button type="button" disabled={busy || !annotationReason.trim()} onClick={() => void annotate()}>保存标注</button>
      </section>
    </>}
  </main>;
}
