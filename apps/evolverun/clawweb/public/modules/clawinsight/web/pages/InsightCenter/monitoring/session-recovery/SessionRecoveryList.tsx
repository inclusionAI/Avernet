import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { sessionRecoveryApi } from '../../../../api/session-recovery';
import { monitoringApi, monitoringErrorText } from '../../../../api/monitoring';
import type { SessionRecoveryListPage } from '../../../../types/session-recovery';
import type { BotOption } from '../../../../types/monitoring';
import { MonitoringBotSelector } from '../MonitoringBotSelector';
import { MonitoringPagination } from '../MonitoringPagination';
import { RecordTime } from '../DiagnosisRecord';
import { MonitoringIcon as Icon } from '../MonitoringIcon';

const statuses: Record<string, { label: string; tone: string }> = {
  running: { label: '执行中', tone: 'bg-blue-50 text-blue-700' },
  waiting_owner: { label: '等待用户处理', tone: 'bg-amber-50 text-amber-800' },
  waiting_effect: { label: '等待效果核验', tone: 'bg-violet-50 text-violet-700' },
  completed: { label: '已完成', tone: 'bg-emerald-50 text-emerald-700' },
  failed: { label: '失败', tone: 'bg-red-50 text-red-700' },
  canceled: { label: '已停止', tone: 'bg-gray-100 text-gray-600' },
};
const effects: Record<string, { label: string; tone: string }> = {
  recovered: { label: '已恢复', tone: 'bg-emerald-50 text-emerald-700' },
  not_recovered: { label: '未恢复', tone: 'bg-red-50 text-red-700' },
  unknown: { label: '尚未确认', tone: 'bg-amber-50 text-amber-800' },
  not_intervened: { label: '未干预', tone: 'bg-gray-100 text-gray-600' },
};
const badgeClass = 'inline-flex w-fit items-center rounded-full px-2.5 py-0.5 text-xs font-medium';
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
  const clearBot = () => update({ recoveryBot: '', recoveryBotRef: '', recoveryEnv: '', recoveryPage: '1' });
  const refresh = () => setRevision(value => value + 1);
  return <>
    <div className="records-title"><h2>自愈记录</h2><span className="sort-note"><Icon name="sort" />最新任务优先</span></div>
    <section className="records-panel recovery-records" aria-label="自愈任务" aria-busy={loading}>
    <div className="records-toolbar recovery-toolbar" role="group" aria-label="自愈任务筛选">
      <MonitoringBotSelector autoSelect={false} selected={selected} isAdmin={false} revision={revision} startDate="" endDate="" onClearFilter={clearBot}
        onSelect={bot => { setSelected(bot); update({ recoveryBot: bot.botId, recoveryBotRef: bot.botRef, recoveryEnv: bot.env, recoveryPage: '1' }); }} onUnavailable={failure => setError(monitoringErrorText(failure))} />
      <select className="recovery-status-filter" aria-label="自愈任务状态" value={status} onChange={event => update({ recoveryStatus: event.target.value, recoveryPage: '1' })}>
        <option value="">全部状态</option>{Object.entries(statuses).map(([value, { label }]) => <option key={value} value={value}>{label}</option>)}
      </select>
      <button type="button" className="refresh" disabled={loading} onClick={refresh}><Icon name="refresh" />刷新</button>
    </div>
    <div className="list-columns" aria-hidden="true"><span>Bot / 会话标识</span><span>任务状态</span><span>恢复结果</span><span>创建时间</span><span /></div>
    {error && <div role="alert" className="error-banner">{error} <button type="button" onClick={refresh}>重试</button></div>}
    {loading && <p className="monitoring-empty">正在加载自愈任务…</p>}
    {data?.total === 0 && <div className="empty-state"><span className="empty-icon"><Icon name="empty" /></span><h3>{botId || status ? '没有符合当前条件的自愈任务。' : '暂无自愈任务'}</h3>
      {(botId || status) && <button type="button" onClick={() => update({ recoveryBot: '', recoveryBotRef: '', recoveryEnv: '', recoveryStatus: '', recoveryPage: '1' })}>清空筛选条件</button>}</div>}
    {data && data.total > 0 && !data.items.length && <div className="empty-state"><p>当前页暂无记录。</p><button type="button" onClick={() => update({ recoveryPage: '1' })}>返回首页</button></div>}
    <div>{data?.items.map(item => {
      const effect = effects[item.effect] ?? effects.unknown;
      return <article className="record" key={item.taskId}>
        <button type="button" className="record-summary" aria-label={`查看详情 ${item.botId} ${item.sessionKey}`} onClick={() => update({ recoveryTaskId: item.taskId })}>
          <span className="record-main"><span className="record-text">
            <span className="record-title-line"><span className="record-title" title={item.botId}>{item.botId}</span><span className="recovery-target mono">{item.entityId} · {item.env}</span></span>
            <span className="record-session mono" title={item.sessionKey}>{item.sessionKey}</span>
          </span></span>
          <span className="recovery-row-status">
            <span className="recovery-field-label">任务状态</span>
            <span className={`${badgeClass} ${statuses[item.status]?.tone ?? 'bg-gray-100 text-gray-600'}`}>{statuses[item.status]?.label ?? item.status}</span>
          </span>
          <span className="recovery-row-effect">
            <span className="recovery-field-label">恢复结果</span>
            <span className={`${badgeClass} ${effect.tone}`}>{effect.label}</span>
          </span>
          <RecordTime value={typeof item.createdAt === 'number' ? new Date(item.createdAt * 1000).toJSON() : item.createdAt} />
          <Icon name="chevron" className="expand-icon" />
        </button>
      </article>;
    })}</div>
    <MonitoringPagination data={data ? { ...data, totalPages: Math.ceil(data.total / data.pageSize) } : null} pageSize={pageSize} loading={loading} resetKey={key}
      onPageChange={value => update({ recoveryPage: String(value) })} onPageSizeChange={value => update({ recoveryPageSize: String(value), recoveryPage: '1' })} />
    </section>
  </>;
}
