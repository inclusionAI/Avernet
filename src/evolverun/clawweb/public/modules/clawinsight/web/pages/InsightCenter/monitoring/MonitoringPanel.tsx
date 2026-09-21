import { useEffect, useRef, useState } from 'react';
import { monitoringApi, monitoringErrorCode, monitoringErrorText } from '../../../api/monitoring';
import type { BotOption, DiagnosisPage, MonitoringQuery } from '../../../types/monitoring';
import { DiagnosisRecord, decisionLabels as labels, displayTime, timeParts } from './DiagnosisRecord';
import { beijingDateRange, MonitoringControls } from './MonitoringControls';
import { MonitoringIcon as Icon } from './MonitoringIcon';
import './monitoring.css';
import { ProblemTypeFilter } from './ProblemTypeFilter';
import { MonitoringEnrollment } from './MonitoringEnrollment';
import { MonitoringPagination } from './MonitoringPagination';

export { displayTime } from './DiagnosisRecord';
const initialQuery = (): MonitoringQuery => ({ ...beijingDateRange(7), decision: 'ALL', keyword: '', page: 1, pageSize: 20 });
const states = { HEALTHY: '监控正常', ERROR: '检查异常', UNKNOWN: '状态未知', PAUSED: '已暂停' };

export default function MonitoringPanel({ isAdmin = false }: { isAdmin?: boolean }) {
  const [selected, setSelected] = useState<BotOption | null>(null);
  const botRef = selected?.botRef ?? '';
  const [botsRevision, setBotsRevision] = useState(0);
  const [query, setQuery] = useState<MonitoringQuery>(initialQuery);
  const [keywordInput, setKeywordInput] = useState('');
  const [revision, setRevision] = useState(0);
  const [loadedPage, setLoadedPage] = useState<{ key: string; value: DiagnosisPage } | null>(null);
  const queryKey = JSON.stringify([botRef, query]);
  // Never render the previous query's rows under the next query's pagination controls.
  const pageData = loadedPage?.key === queryKey ? loadedPage.value : null;
  const pageCorrectionUsed = useRef(false);
  const [status, setStatus] = useState<BotOption | null>(null);
  const [error, setError] = useState('');
  const [statusError, setStatusError] = useState('');
  const [loading, setLoading] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [openIds, setOpenIds] = useState<Set<string>>(new Set());
  const requestSequence = useRef(0);
  const invalidDates = Boolean(query.startDate && query.endDate && query.startDate > query.endDate);

  useEffect(() => {
    const timer = setTimeout(() => {
      if (query.keyword === keywordInput.trim()) return;
      pageCorrectionUsed.current = false;
      setQuery(current => ({ ...current, keyword: keywordInput.trim(), page: 1 }));
    }, 300);
    return () => clearTimeout(timer);
  }, [keywordInput, query.keyword]);

  // A changed query clears unrelated results. Polling preserves the current page and expanded records.
  useEffect(() => { setLoadedPage(null); setOpenIds(new Set()); }, [botRef, query]);
  useEffect(() => { setStatus(null); if (botRef) { setStatusError(''); setError(''); } setUpdatedAt(null); }, [botRef]);

  useEffect(() => {
    if (!botRef) return;
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
      const request = async () => {
        if (invalidDates) return;
        let value: BotOption;
        try {
          value = await monitoringApi.targetStatus(botRef, query, current.signal);
          if (!live()) return;
          setStatus(value); setStatusError('');
          if (value.enrollmentState !== 'ENROLLED') { setLoadedPage(null); setError(''); return; }
        } catch (failure) {
          if (live()) {
            setStatusError(monitoringErrorText(current.signal.reason ?? failure));
            const code = (failure as { status?: number }).status;
            if ([401, 403, 404].includes(code ?? 0)) { setSelected(null); setStatus(null); setLoadedPage(null); setOpenIds(new Set()); setBotsRevision(x => x + 1); }
          }
          return;
        }
        try {
          const page = await monitoringApi.targetDiagnoses(botRef, query, current.signal);
          if (!live()) return;
          if (query.page > Math.max(1, page.totalPages) && !pageCorrectionUsed.current) {
            pageCorrectionUsed.current = true;
            setQuery(previous => ({ ...previous, page: Math.max(1, page.totalPages) }));
            return;
          }
          setLoadedPage({ key: queryKey, value: page }); setError(''); setUpdatedAt(new Date().toISOString());
        } catch (failure) {
          if (live()) {
            if ((failure as { status?: number }).status === 409 && monitoringErrorCode(failure) === 'MONITORING_NOT_ENROLLED') {
              setLoadedPage(null); setOpenIds(new Set()); setError('');
              setStatus(previous => previous ? { ...previous, enrollmentState: 'NOT_ENROLLED', monitoring: null,
                capabilities: { canView: true, canRequestEnrollment: true } } : null);
              setBotsRevision(x => x + 1); return;
            }
            setError(monitoringErrorText(current.signal.reason ?? failure));
            if ([401, 403, 404].includes((failure as { status?: number }).status ?? 0)) {
              setSelected(null); setStatus(null); setLoadedPage(null); setOpenIds(new Set()); setBotsRevision(x => x + 1);
            }
          }
        }
      };
      void request().finally(() => { if (live()) { clearTimeout(timer); setLoading(false); } });
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
  }, [botRef, query, queryKey, revision, invalidDates]);

  const change = (patch: Partial<MonitoringQuery>) => { pageCorrectionUsed.current = false; setError(''); setQuery(current => ({ ...current, ...patch, page: patch.page ?? 1 })); };
  const countKey = { ALL: 'all', ALERT: 'alert', PASS: 'pass', UNRESOLVED: 'unresolved' } as const;

  const filtered = Boolean(query.businessProblemCategory || query.businessProblemSubtype || query.startDate || query.endDate || query.keyword || query.decision !== 'ALL');
  const clearFilters = () => { setKeywordInput(''); change({ ...initialQuery(), businessProblemCategory: '', businessProblemSubtype: '', pageSize: query.pageSize }); };
  const lastCheck = timeParts(status?.monitoring?.lastSuccessfulCheckAt ?? null);
  const healthClass = statusError ? 'unknown' : status?.monitoring?.status === 'HEALTHY' ? '' : status?.monitoring?.status.toLowerCase() ?? 'unknown';
  return <div className="insight-monitoring">
    <div className="page-heading"><div><h1>Agent 监控自愈</h1><p>关注运行异常，查看每一次会话诊断。</p></div></div>
    <MonitoringControls selected={selected} isAdmin={isAdmin} revision={botsRevision} loading={loading} startDate={query.startDate} endDate={query.endDate}
      onBotChange={bot => { if (bot.botRef !== botRef) { setSelected(bot); setStatus(null); change({}); } }} onDatesChange={change}
      onUnavailable={failure => { ++requestSequence.current; setLoading(false); setSelected(null); setStatus(null); setLoadedPage(null); setOpenIds(new Set()); setStatusError(monitoringErrorText(failure)); }}
      onRefresh={() => { setBotsRevision(x => x + 1); setRevision(x => x + 1); }}>
      {botRef && <>
        <div className="status-bar" role="region" aria-label="Bot 监控状态">
          <div className="status-left"><span className={`health ${healthClass}`}><span className="dot" />{statusError ? '状态未更新' : status ? status.monitoring ? states[status.monitoring.status] : '未监控' : '状态加载中…'}</span><span className="separator" /><span className="status-time" title={displayTime(status?.monitoring?.lastSuccessfulCheckAt ?? null)}>最近成功检查 {lastCheck ? `${lastCheck.day} ${lastCheck.time}` : '—'}</span></div>
          <span className="live-refresh" title="页面可见时每 30 秒自动刷新"><Icon name="refresh" />{loading ? '正在更新…' : updatedAt ? `更新于 ${timeParts(updatedAt)?.time}` : '每 30 秒刷新'}</span>
        </div>
        {statusError && <div className="status-errors"><p role="alert" className="error-banner">{statusError}{pageData && ' 当前保留上次结果，尚未更新。'}</p></div>}
      </>}
    </MonitoringControls>
    {!botRef && <p className="monitoring-empty">选择一个 Bot，查看监控状态和诊断结果。</p>}
    {!botRef && (statusError || error) && <p role="alert" className="error-banner">{statusError || error}</p>}
    {status?.enrollmentState === 'NOT_ENROLLED' && selected && <MonitoringEnrollment key={botRef} bot={status} onRefresh={() => setRevision(x => x + 1)} />}
    {botRef && status?.enrollmentState === 'ENROLLED' && <>
      <div className="records-title"><h2>诊断记录</h2><span className="sort-note" title="按会话时间倒序排列，时间未知的记录在最后"><Icon name="sort" />最新会话优先</span></div>
      <section aria-label="诊断记录" className="records-panel" aria-busy={loading}>
        <div className="records-toolbar">
          <div role="group" aria-label="诊断结果筛选" className="result-tabs">{(Object.keys(labels) as (keyof typeof labels)[]).map(value => <button type="button" aria-pressed={query.decision === value} key={value} onClick={() => change({ decision: value })} className={query.decision === value ? 'active' : ''}>{labels[value]} <span className="number">{pageData ? pageData.counts[countKey[value]] : '—'}</span></button>)}</div>
          <ProblemTypeFilter key={`${botRef}:${query.startDate}:${query.endDate}`} options={pageData?.problemTypes ?? []}
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
        <MonitoringPagination data={pageData} pageSize={query.pageSize} loading={loading || invalidDates}
          resetKey={queryKey} onPageChange={page => change({ page })} onPageSizeChange={pageSize => change({ pageSize })} />
      </section>
    </>}
  </div>;
}
