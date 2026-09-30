import { useEffect, useState } from 'react';
import { fetchJson } from '@avernet/clawweb-shared/web/api/client';

type Candidate = { id: string; summary: string; sources: Array<{ flowId: string; analysisId: string; reasoning: string }> };
type GroupRepairData = { capability: string; inputDigest: string | null; candidates: Candidate[]; uncovered: number;
  bots: Array<{ botId: string; botName?: string; env: string | null }>;
  tasks: Array<{ taskId: string; status: string; summary: string; progress?: { message: string }; selection: { candidates: Candidate[] };
    deployResult?: { deployed: boolean }; outcomes: Array<{ id: string; status: string; reason: string }> }> };

export default function GroupRepairSelection({ workflowId, signature }: { workflowId: string; signature: string }) {
  const [data, setData] = useState<GroupRepairData>();
  const [selection, setSelection] = useState<{ ids: string[]; digest: string | null }>();
  const [bot, setBot] = useState('');
  const [instructions, setInstructions] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const result = await fetchJson<GroupRepairData>(`/api/evolve/group-repairs?${new URLSearchParams({ workflowId, signature })}`);
        if (disposed) return;
        if (result.capability !== 'issue-group-repair/v1') throw new Error('服务端尚不支持问题组修复');
        setData(result);
        setError('');
        setSelection(previous => previous ?? { ids: result.candidates.map(candidate => candidate.id), digest: result.inputDigest });
      } catch (err) { if (!disposed) setError(err instanceof Error ? err.message : '加载失败'); }
      finally { if (!disposed) timer = setTimeout(refresh, 10_000); }
    };
    void refresh();
    return () => { disposed = true; clearTimeout(timer); };
  }, [workflowId, signature, revision]);
  const selected = data?.candidates.filter(candidate => selection?.ids.includes(candidate.id)) ?? [];
  const changed = !!data && selection?.digest !== data.inputDigest;
  const active = data?.tasks.some(task => !['completed', 'succeeded', 'failed', 'canceled', 'cancelled'].includes(task.status));
  const botEntry = data?.bots.find(entry => JSON.stringify([entry.botId, entry.env]) === bot);
  const canSubmit = !!data && selected.length > 0 && selected.length <= 20 && !changed && !error && !active && !!botEntry && !submitting;
  const submit = async () => {
    if (!canSubmit || !botEntry) return;
    setSubmitting(true);
    try {
      await fetchJson('/api/evolve/group-repairs', { method: 'POST', body: JSON.stringify({ workflowId, signature, inputDigest: data.inputDigest,
        candidateIds: selected.map(candidate => candidate.id), botId: botEntry.botId, botEnv: botEntry.env,
        applicationSpec: instructions.trim() || '结合当前 YAML 统一处理所选建议；遇到无法确认的冲突停止部署，逐项说明处理结果。' }) });
      setNotice('已派发组级修复任务，应用完成后仍需验证效果。');
      setConfirming(false);
      setRevision(value => value + 1);
    } catch (err) { setError(err instanceof Error ? err.message : '派发失败'); }
    finally { setSubmitting(false); }
  };
  return <section aria-label="本次修复范围" className="mt-5 space-y-3 rounded-lg border border-slate-200 p-3">
    <h4 className="text-sm font-semibold">本次修复范围</h4>
    <p className="text-xs text-slate-500">默认选中最新分析的去重建议。Bot 会统一判断，不会依次执行旧补丁；切换运行只改变下方详情。</p>
    {error && <p role="alert" className="text-xs text-red-600">{error}<button onClick={() => setRevision(value => value + 1)} className="ml-2 underline">重试</button></p>}
    {!data && !error && <p className="text-xs">正在加载建议…</p>}
    {data && <>
      {changed && <div role="status" className="rounded bg-amber-50 p-2 text-xs">分析来源已更新，新建议未自动加入修复范围。请重新核对。<button className="ml-2 underline" onClick={() => { setSelection({ ids: selected.map(candidate => candidate.id), digest: data.inputDigest }); setConfirming(false); }}>已核对更新</button></div>}
      <div className="flex items-center gap-3 text-xs"><span>已选 {selected.length} / 共 {data.candidates.length}</span>
        <button onClick={() => { setSelection({ ids: data.candidates.map(candidate => candidate.id), digest: selection?.digest ?? data.inputDigest }); setConfirming(false); }}>全选</button>
        <button onClick={() => { setSelection({ ids: [], digest: selection?.digest ?? data.inputDigest }); setConfirming(false); }}>清空</button></div>
      {data.candidates.map(candidate => <div key={candidate.id} className="rounded border border-slate-100 p-2 text-xs">
        <label className="flex gap-2"><input type="checkbox" checked={selected.some(item => item.id === candidate.id)} onChange={event => {
          setSelection({ ids: event.target.checked ? [...selected.map(item => item.id), candidate.id] : selected.filter(item => item.id !== candidate.id).map(item => item.id), digest: selection?.digest ?? data.inputDigest }); setConfirming(false);
        }} /><span>{candidate.summary}</span></label>
        <details className="ml-5 mt-1 text-slate-500"><summary>来源：{new Set(candidate.sources.map(source => source.flowId)).size} 个运行</summary>{candidate.sources.map(source => <p key={`${source.analysisId}-${source.flowId}`} className="mt-1 break-all">{source.analysisId} · {source.flowId}<br />{source.reasoning}</p>)}</details>
      </div>)}
      {!data.candidates.length && <p className="text-xs">本组最新分析尚无候选建议。</p>}
      {!!data.uncovered && <p className="text-xs text-amber-700">另有 {data.uncovered} 条诊断未生成建议，不在本次自动修复范围。</p>}
      {selected.length > 20 && <p className="text-xs text-amber-700">单次最多选择 20 条，请明确缩小范围，不会自动截断。</p>}
      <label className="block text-xs">执行 Bot<select aria-label="执行 Bot" value={bot} onChange={event => setBot(event.target.value)} className="mt-1 w-full rounded border p-2"><option value="">请选择有编辑权限的 Bot</option>{data.bots.map(entry => <option key={JSON.stringify([entry.botId, entry.env])} value={JSON.stringify([entry.botId, entry.env])}>{entry.botName || entry.botId} · {entry.env}</option>)}</select></label>
      <label className="block text-xs">补充要求（可选）<textarea value={instructions} onChange={event => { setInstructions(event.target.value); setConfirming(false); }} maxLength={20000} rows={3} className="mt-1 w-full rounded border p-2" /></label>
      {confirming && <div className="rounded bg-amber-50 p-3 text-xs"><p>确认修复以下 {selected.length} 条建议，排除 {data.candidates.length - selected.length} 条；允许 Bot 修改并部署，结果仍待效果验证。</p><ul className="mt-2 list-inside list-disc">{selected.map(item => <li key={item.id}>{item.summary}</li>)}</ul><button disabled={!canSubmit} onClick={() => void submit()} className="mt-2 rounded bg-blue-600 px-3 py-2 text-white disabled:opacity-50">{submitting ? '派发中…' : '确认修复并部署'}</button></div>}
      {!confirming && <button disabled={!canSubmit} onClick={() => setConfirming(true)} className="rounded bg-blue-600 px-3 py-2 text-xs text-white disabled:opacity-50">修复所选问题（{selected.length} 条建议）</button>}
      {active && <p className="text-xs text-amber-700">本组已有进行中的修复任务。</p>}
      {notice && <p role="status" className="text-xs text-blue-700">{notice}</p>}
      {!!data.tasks.length && <div className="border-t pt-3"><h5 className="text-xs font-semibold">修复任务记录（不等于效果验证）</h5>{data.tasks.map(task => <div key={task.taskId} className="mt-2 text-xs"><p className="break-all">{task.taskId} · {task.status}</p><p>{task.progress?.message || task.summary}</p>{task.deployResult && <p>{task.deployResult.deployed ? '已部署，待验证' : '未部署'}</p>}{task.outcomes.map(outcome => <p key={outcome.id} className="mt-1">{task.selection.candidates.find(candidate => candidate.id === outcome.id)?.summary}：{outcome.status === 'applied' ? '已处理' : outcome.status === 'not_needed' ? '无需修改' : '未处理'} — {outcome.reason}</p>)}</div>)}</div>}
    </>}
  </section>;
}
