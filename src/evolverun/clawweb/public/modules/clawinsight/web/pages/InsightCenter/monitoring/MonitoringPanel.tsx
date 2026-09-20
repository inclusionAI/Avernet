import { useEffect, useRef, useState } from 'react';
import { monitoringApi, monitoringErrorText } from '../../../api/monitoring';
import type { BotStatus, DiagnosisPage, MonitoringQuery } from '../../../types/monitoring';
import { DiagnosisRecord, decisionLabels as labels, displayTime, timeParts } from './DiagnosisRecord';
import { beijingDateRange, MonitoringControls } from './MonitoringControls';
import { MonitoringIcon as Icon } from './MonitoringIcon';
import './monitoring.css';
import { ProblemTypeFilter } from './ProblemTypeFilter';

export { displayTime } from './DiagnosisRecord';
const initialQuery = (): MonitoringQuery => ({ ...beijingDateRange(1), decision: 'ALL', keyword: '', page: 1, pageSize: 20 });
const states = { HEALTHY: '监控正常', ERROR: '检查异常', UNKNOWN: '状态未知', PAUSED: '已暂停' };

export default function MonitoringPanel() {
  const [bots, setBots] = useState<{ botId: string }[]>([]);
  const [botId, setBotId] = useState('');
  const [botsError, setBotsError] = useState('');
  const [botsLoading, setBotsLoading] = useState(true);
  const [botsRevision, setBotsRevision] = useState(0);
  const [query, setQuery] = useState<MonitoringQuery>(initialQuery);
  const [keywordInput, setKeywordInput] = useState('');
  const [revision, setRevision] = useState(0);
  const [pageData, setPageData] = useState<DiagnosisPage | null>(null);
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [error, setError] = useState('');
  const [statusError, setStatusError] = useState('');
  const [loading, setLoading] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [openIds, setOpenIds] = useState<Set<string>>(new Set());
  const requestSequence = useRef(0);
  const invalidDates = Boolean(query.startDate && query.endDate && query.startDate > query.endDate);

  useEffect(() => {
    let active = true;
    let controller: AbortController | null = null;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const load = () => {
      if (document.visibilityState === 'hidden') return;
      controller?.abort(); clearTimeout(timeout);
      const current = new AbortController(); controller = current;
      const live = () => active && controller === current;
      timeout = setTimeout(() => current.abort(new DOMException('Timeout', 'TimeoutError')), 15000);
      setBotsLoading(true); setBotsError('');
      monitoringApi.bots(current.signal).then(result => {
        if (!live()) return;
        setBots(result.items);
        setBotId(selected => result.items.some(bot => bot.botId === selected) ? selected : result.items[0]?.botId ?? '');
      }).catch(failure => { if (live()) setBotsError(monitoringErrorText(current.signal.reason ?? failure)); })
        .finally(() => { if (live()) { clearTimeout(timeout); setBotsLoading(false); } });
    };
    const visibility = () => {
      if (document.visibilityState === 'hidden') {
        controller?.abort(); controller = null; clearTimeout(timeout); setBotsLoading(false);
      } else load();
    };
    load();
    const interval = setInterval(load, 30000);
    document.addEventListener('visibilitychange', visibility);
    return () => { active = false; controller?.abort(); clearTimeout(timeout); clearInterval(interval); document.removeEventListener('visibilitychange', visibility); };
  }, [botsRevision]);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(current => current.keyword === keywordInput.trim() ? current : { ...current, keyword: keywordInput.trim(), page: 1 }), 300);
    return () => clearTimeout(timer);
  }, [keywordInput]);

  // A changed query clears unrelated results. Polling preserves the current page and expanded records.
  useEffect(() => { setPageData(null); setOpenIds(new Set()); }, [botId, query]);
  useEffect(() => { setStatus(null); setStatusError(''); setUpdatedAt(null); }, [botId]);

  useEffect(() => {
    if (!botId) return;
    let disposed = false;
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () => {
      if (document.visibilityState === 'hidden') return;
      controller?.abort(); clearTimeout(timer);
      const current = new AbortController(); controller = current;
      const sequence = ++requestSequence.current;
      const live = () => !disposed && sequence === requestSequence.current;
      timer = setTimeout(() => current.abort(new DOMException('Timeout', 'TimeoutError')), 15000);
      setLoading(true);
      const statusRequest = monitoringApi.status(botId, current.signal).then(value => {
        if (live()) { setStatus(value); setStatusError(''); }
      }).catch(failure => { if (live()) setStatusError(monitoringErrorText(current.signal.reason ?? failure)); });
      const listRequest = invalidDates ? Promise.resolve() : monitoringApi.diagnoses(botId, query, current.signal).then(value => {
        if (live()) { setPageData(value); setError(''); setUpdatedAt(new Date().toISOString()); }
      }).catch(failure => { if (live()) setError(monitoringErrorText(current.signal.reason ?? failure)); });
      void Promise.all([statusRequest, listRequest]).finally(() => { if (live()) { clearTimeout(timer); setLoading(false); } });
    };
    const visibility = () => {
      if (document.visibilityState === 'hidden') {
        ++requestSequence.current; controller?.abort(); clearTimeout(timer); setLoading(false);
      } else load();
    };
    load();
    const interval = setInterval(load, 30000);
    document.addEventListener('visibilitychange', visibility);
    return () => { disposed = true; controller?.abort(); clearTimeout(timer); clearInterval(interval); document.removeEventListener('visibilitychange', visibility); };
  }, [botId, query, revision, invalidDates]);

  const change = (patch: Partial<MonitoringQuery>) => { setError(''); setQuery(current => ({ ...current, ...patch, page: patch.page ?? 1 })); };
  const countKey = { ALL: 'all', ALERT: 'alert', PASS: 'pass', UNRESOLVED: 'unresolved' } as const;
  const pages = pageData?.totalPages ?? 0;
  const pageNumbers = Array.from({ length: Math.min(5, pages) }, (_, i) => Math.max(1, Math.min(query.page - 2, pages - 4)) + i);

  const filtered = Boolean(query.businessProblemCategory || query.businessProblemSubtype || query.startDate || query.endDate || query.keyword || query.decision !== 'ALL');
  const clearFilters = () => { setKeywordInput(''); change({ ...initialQuery(), businessProblemCategory: '', businessProblemSubtype: '', pageSize: query.pageSize }); };
  const lastCheck = timeParts(status?.lastSuccessfulCheckAt ?? null);
  const healthClass = statusError ? 'unknown' : status?.status === 'HEALTHY' ? '' : status?.status.toLowerCase() ?? 'unknown';
  return <div className="insight-monitoring">
    <div className="page-heading"><div><h1>Agent 监控自愈</h1><p>关注运行异常，查看每一次会话诊断。</p></div></div>
    <MonitoringControls bots={bots} botId={botId} botsLoading={botsLoading} loading={loading} startDate={query.startDate} endDate={query.endDate}
      onBotChange={id => { setBotId(id); change({}); }} onDatesChange={change}
      onRefresh={() => { setBotsRevision(x => x + 1); setRevision(x => x + 1); }}>
      {botId && <>
        <div className="status-bar" role="region" aria-label="Bot 监控状态">
          <div className="status-left"><span className={`health ${healthClass}`}><span className="dot" />{statusError ? '状态未更新' : status ? states[status.status] : '状态加载中…'}</span><span className="separator" /><span className="status-time" title={displayTime(status?.lastSuccessfulCheckAt ?? null)}>最近成功检查 {lastCheck ? `${lastCheck.day} ${lastCheck.time}` : '—'}</span></div>
          <span className="live-refresh" title="页面可见时每 30 秒自动刷新"><Icon name="refresh" />{loading ? '正在更新…' : updatedAt ? `更新于 ${timeParts(updatedAt)?.time}` : '每 30 秒刷新'}</span>
        </div>
        {statusError && <div className="status-errors"><p role="alert" className="error-banner">{statusError}</p></div>}
      </>}
    </MonitoringControls>
    {botsError && <p role="alert" className="error-banner">{botsError}</p>}
    {!botsLoading && !botsError && !bots.length && <p className="monitoring-empty">暂无已上报的监控 Bot。</p>}
    {botId && <>
      <div className="records-title"><h2>诊断记录</h2><span className="sort-note" title="按会话时间倒序排列，时间未知的记录在最后"><Icon name="sort" />最新会话优先</span></div>
      <section aria-label="诊断记录" className="records-panel" aria-busy={loading}>
        <div className="records-toolbar">
          <div role="group" aria-label="诊断结果筛选" className="result-tabs">{(Object.keys(labels) as (keyof typeof labels)[]).map(value => <button type="button" aria-pressed={query.decision === value} key={value} onClick={() => change({ decision: value })} className={query.decision === value ? 'active' : ''}>{labels[value]} <span className="number">{pageData ? pageData.counts[countKey[value]] : '—'}</span></button>)}</div>
          <ProblemTypeFilter key={`${botId}:${query.startDate}:${query.endDate}`} options={pageData?.problemTypes ?? []}
            category={query.businessProblemCategory ?? ''} subtype={query.businessProblemSubtype ?? ''}
            onApply={(businessProblemCategory, businessProblemSubtype) => change({ businessProblemCategory, businessProblemSubtype })} />
          <label className="search-box"><Icon name="search" /><input type="search" aria-label="搜索诊断记录" placeholder="搜索 Session Key / Trace ID" maxLength={200} value={keywordInput} onChange={event => setKeywordInput(event.target.value)} />{keywordInput && <button type="button" className="search-clear" aria-label="清空搜索" onClick={() => { setKeywordInput(''); change({ keyword: '' }); }}><Icon name="x" /></button>}</label>
        </div>
        <div className="list-columns" aria-hidden="true"><span>诊断结论 / 会话标识</span><span>TC 故障标签</span><span>会话时间</span><span /></div>
        {error && <div role="alert" className="error-banner">{error}{pageData && ' 当前保留上次结果，尚未更新。'} <button type="button" onClick={() => { setBotsRevision(x => x + 1); setRevision(x => x + 1); }}>重试</button></div>}
        {invalidDates ? <p className="monitoring-empty">请先调整日期范围。</p> : <div>
          {pageData?.items.map(item => <DiagnosisRecord key={item.diagnosisId} item={item} open={openIds.has(item.diagnosisId)} toggle={() => setOpenIds(current => { const next = new Set(current); if (next.has(item.diagnosisId)) next.delete(item.diagnosisId); else next.add(item.diagnosisId); return next; })} />)}
          {!pageData && !error && <p className="monitoring-empty">正在加载诊断记录…</p>}
          {pageData?.total === 0 && <div className="empty-state"><span className="empty-icon"><Icon name="empty" /></span><h3>{filtered ? '没有符合当前条件的诊断记录。' : '暂无诊断记录'}</h3><p>{filtered ? '试试调整时间范围、问题类型、诊断结论或搜索关键词。' : '此 Bot 还没有可展示的诊断结果。'}</p>{filtered && <button type="button" onClick={clearFilters}>清空筛选条件</button>}</div>}
          {pageData && pageData.total > 0 && !pageData.items.length && <div className="empty-state"><p>当前页暂无记录。</p><button type="button" onClick={() => change({ page: 1 })}>返回首页</button></div>}
        </div>}
        <footer className="list-footer">
          <span aria-live="polite">{pageData?.items.length ? `第 ${(pageData.page - 1) * pageData.pageSize + 1}–${(pageData.page - 1) * pageData.pageSize + pageData.items.length} 条，共 ${pageData.total} 条` : pageData?.total === 0 ? '共 0 条' : '—'}</span>
          <nav aria-label="诊断记录分页" className="pagination"><select aria-label="每页条数" value={query.pageSize} onChange={event => change({ pageSize: Number(event.target.value) })}>{[10, 20, 50].map(n => <option key={n} value={n}>{n} 条 / 页</option>)}</select>
            <button type="button" aria-label="上一页" className="page-btn" disabled={loading || query.page <= 1 || !pages} onClick={() => change({ page: query.page - 1 })}><Icon name="arrow" /></button>
            {pageNumbers.map(n => <button type="button" key={n} aria-label={`第 ${n} 页`} aria-current={query.page === n ? 'page' : undefined} className={`page-btn ${query.page === n ? 'active' : ''}`} onClick={() => change({ page: n })}>{n}</button>)}
            <button type="button" aria-label="下一页" className="page-btn" disabled={loading || query.page >= pages} onClick={() => change({ page: query.page + 1 })}><Icon name="arrow" className="next-arrow" /></button>
          </nav>
        </footer>
      </section>
    </>}
  </div>;
}
