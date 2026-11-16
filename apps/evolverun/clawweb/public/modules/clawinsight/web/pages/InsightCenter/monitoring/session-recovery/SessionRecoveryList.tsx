import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { sessionRecoveryApi } from '../../../../api/session-recovery';
import { monitoringApi, monitoringErrorText } from '../../../../api/monitoring';
import type { SessionRecoveryListPage } from '../../../../types/session-recovery';
import type { BotOption } from '../../../../types/monitoring';
import { MonitoringBotSelector } from '../MonitoringBotSelector';
import { MonitoringPagination } from '../MonitoringPagination';
import { displayTime } from '../DiagnosisRecord';

const statuses: Record<string, string> = { running: '执行中', waiting_owner: '等待用户处理', waiting_effect: '等待效果核验', completed: '已完成', failed: '失败', canceled: '已停止' };
const effects: Record<string, string> = { recovered: '已恢复', not_recovered: '未恢复', unknown: '尚未确认', not_intervened: '未干预' };
export function SessionRecoveryList() {
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<BotOption | null>(null);
  const [data, setData] = useState<SessionRecoveryListPage | null>(null);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(false);
  const page = Math.max(1, Number(params.get('recoveryPage')) || 1), pageSize = Number(params.get('recoveryPageSize')) || 20;
  const botId = params.get('recoveryBot') ?? '', status = params.get('recoveryStatus') ?? '', botRef = params.get('recoveryBotRef');
  const env = params.get('recoveryEnv') ?? '';
  const key = JSON.stringify([page, pageSize, botId, env, status]);
  const update = (values: Record<string, string>) => { const next = new URLSearchParams(params); for (const [k, v] of Object.entries(values)) { if (v) next.set(k, v); else next.delete(k); } setParams(next); };
  useEffect(() => {
    if (!botRef) { setSelected(null); return; }
    const controller = new AbortController();
    monitoringApi.targetStatus(botRef, { startDate: '', endDate: '' }, controller.signal).then(value => { if (!controller.signal.aborted) setSelected(value); }).catch(() => {});
    return () => controller.abort();
  }, [botRef]);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setData(null); setError('');
    sessionRecoveryApi.tasks({ page, pageSize, botId, env, status }, controller.signal).then(value => { if (!controller.signal.aborted) setData(value); })
      .catch(failure => { if (!controller.signal.aborted) setError(monitoringErrorText(failure)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [page, pageSize, botId, env, status, revision]);
  return <section className="records-panel" aria-label="自愈任务" aria-busy={loading}>
    <div className="recovery-list-toolbar"><MonitoringBotSelector autoSelect={false} selected={selected} isAdmin={false} revision={0} startDate="" endDate=""
      onSelect={bot => { setSelected(bot); update({ recoveryBot: bot.botId, recoveryBotRef: bot.botRef, recoveryEnv: bot.env, recoveryPage: '1' }); }} onUnavailable={failure => setError(monitoringErrorText(failure))} />
      <button type="button" onClick={() => { setSelected(null); update({ recoveryBot: '', recoveryBotRef: '', recoveryEnv: '', recoveryPage: '1' }); }}>全部 Bot</button>
      <select aria-label="自愈任务状态" value={status} onChange={event => update({ recoveryStatus: event.target.value, recoveryPage: '1' })}><option value="">全部状态</option>{Object.entries(statuses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
      <button type="button" disabled={loading} onClick={() => setRevision(value => value + 1)}>刷新</button></div>
    {error && <p className="error-banner" role="alert">{error}</p>}
    {loading && <p className="monitoring-empty">正在加载自愈任务…</p>}
    {data?.total === 0 && <p className="monitoring-empty">暂无符合条件的自愈任务。</p>}
    {!!data?.items.length && <div className="recovery-list-table"><table><thead><tr><th>Bot / 会话</th><th>任务状态</th><th>恢复效果</th><th>创建时间</th><th>操作</th></tr></thead><tbody>{data.items.map(item => <tr key={item.taskId}>
      <td><strong>{item.botId}</strong><small>{item.entityId} · {item.env}</small><small title={item.sessionKey}>{item.sessionKey}</small></td>
      <td>{statuses[item.status] ?? item.status}</td><td>{effects[item.effect] ?? '尚未确认'}</td><td>{displayTime(typeof item.createdAt === 'number' ? new Date(item.createdAt * 1000).toISOString() : item.createdAt)}</td>
      <td><button type="button" onClick={() => update({ recoveryTaskId: item.taskId })}>查看详情</button></td></tr>)}</tbody></table></div>}
    <MonitoringPagination data={data ? { ...data, totalPages: Math.ceil(data.total / data.pageSize) } : null} pageSize={pageSize} loading={loading} resetKey={key}
      onPageChange={value => update({ recoveryPage: String(value) })} onPageSizeChange={value => update({ recoveryPageSize: String(value), recoveryPage: '1' })} />
  </section>;
}
