import { useEffect, useId, useRef, useState } from 'react';
import { monitoringApi, monitoringErrorText } from '../../../api/monitoring';
import type { BotOption, BotScope } from '../../../types/monitoring';
import { MonitoringIcon as Icon } from './MonitoringIcon';
import { useBotPickerLayout } from './useBotPickerLayout';

type Props = {
  selected: BotOption | null; isAdmin: boolean; startDate: string; endDate: string;
  revision: number; onSelect: (bot: BotOption) => void; onUnavailable: (error: unknown) => void;
};
export function MonitoringBotSelector({ selected, isAdmin, startDate, endDate, revision, onSelect, onUnavailable }: Props) {
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState<BotScope>('mine');
  const [draft, setDraft] = useState(''), [q, setQ] = useState('');
  const [composing, setComposing] = useState(false);
  const [items, setItems] = useState<BotOption[]>([]);
  const context = JSON.stringify([scope, q, startDate, endDate, revision]);
  const [position, setPosition] = useState<{ context: string; value: string } | null>(null);
  const cursor = position?.context === context ? position.value : null;
  const setCursor = (value: string | null) => setPosition(value ? { context, value } : null);
  const [loadedContext, setLoadedContext] = useState('');
  const visibleItems = loadedContext === context && draft.trim() === q ? items : [];
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false), [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const root = useRef<HTMLDivElement>(null), trigger = useRef<HTMLButtonElement>(null), input = useRef<HTMLInputElement>(null);
  const picker = useRef<HTMLDivElement>(null);
  const pickerStyle = useBotPickerLayout(open, root, picker);
  const autoSelected = useRef(false), callback = useRef(onSelect);
  callback.current = onSelect;
  const unavailable = useRef(onUnavailable); unavailable.current = onUnavailable;
  const id = useId();
  useEffect(() => {
    if (composing) return;
    const timer = setTimeout(() => { setQ(draft.trim()); setCursor(null); }, 250);
    return () => clearTimeout(timer);
  }, [draft, composing]);
  useEffect(() => {
    const controller = new AbortController(); let active = true;
    const timer = setTimeout(() => controller.abort(new DOMException('Timeout', 'TimeoutError')), 15000);
    setLoading(true); setError('');
    if (!cursor) setItems([]);
    monitoringApi.options(scope, q, { startDate, endDate }, cursor, controller.signal).then(page => {
      if (!active) return;
      setItems(previous => cursor ? [...previous, ...page.items.filter(b => !previous.some(p => p.botRef === b.botRef))] : page.items);
      setNextCursor(page.nextCursor); setLoadedContext(context);
      if (!autoSelected.current && scope === 'mine' && !q && page.items[0]) {
        autoSelected.current = true;
        callback.current(page.items[0]);
      }
    }).catch(e => { if (active) { if ([401, 403, 404].includes(e?.status)) { setItems([]); unavailable.current(e); } setError(monitoringErrorText(controller.signal.reason ?? e)); setNextCursor(null); } })
      .finally(() => { if (active) { clearTimeout(timer); setLoading(false); } });
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [scope, q, cursor, startDate, endDate, revision, retry, context]);
  useEffect(() => {
    if (!open) return;
    input.current?.focus();
    const dismiss = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); setOpen(false); trigger.current?.focus(); } };
    document.addEventListener('pointerdown', dismiss); document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('keydown', escape); };
  }, [open]);
  return <div className="control-block bot" ref={root}>
    <span className="label">监控 Bot</span>
    <button type="button" ref={trigger} className="control-button bot-trigger" aria-label={`监控 Bot ${selected?.botName ?? '选择 Bot'}`}
      title={selected ? `${selected.botId} · ${selected.ownerId} · ${selected.env}` : undefined} aria-expanded={open} aria-controls={`${id}-picker`} onClick={() => { setOpen(!open); if (!open) { setDraft(''); setQ(''); setScope('mine'); setCursor(null); setRetry(n => n + 1); } }}>
      <span className="bot-glyph"><Icon name="bot" /></span><span className="trigger-text"><strong>{selected?.botName ?? '选择监控 Bot'}</strong>
        <span className="mono">{selected ? `${selected.botId} · ${selected.ownerId}` : '搜索或选择你的 Bot'}</span></span><Icon name="chevron" className="chevron" />
    </button>
    {open && <div ref={picker} style={pickerStyle} className="popover bot-picker" id={`${id}-picker`} aria-label="选择目标 Bot">
      <div className="picker-heading">选择目标 Bot</div>
      <label className="bot-search"><Icon name="search" /><input ref={input} type="search" aria-label="搜索 Bot" maxLength={200}
        placeholder={isAdmin ? '搜索 Bot 名称、Bot ID 或工号' : '搜索 Bot 名称或 Bot ID'} value={draft} onKeyDown={e => { if (e.key === 'ArrowDown' && !e.nativeEvent.isComposing && !composing) { e.preventDefault(); root.current?.querySelector<HTMLButtonElement>('.bot-card')?.focus(); } }} onCompositionStart={() => setComposing(true)} onCompositionEnd={e => { setDraft(e.currentTarget.value); setComposing(false); }} onChange={e => setDraft(e.target.value)} /></label>
      {isAdmin && <div className="scope-switch" role="group" aria-label="Bot 范围">{(['mine', 'monitored', 'all'] as const).map(value => <button type="button" key={value}
        aria-pressed={scope === value} onClick={() => { setScope(value); setCursor(null); }}>{({ mine: '我的 Bot', monitored: '诊断范围', all: '全部 Bot' })[value]}</button>)}</div>}
      <p className="summary-period">所选会话时间内的诊断摘要</p>
      <div className="bot-candidates" aria-busy={loading} onKeyDown={e => {
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
        const buttons = [...e.currentTarget.querySelectorAll<HTMLButtonElement>('.bot-card')];
        const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
        if (current < 0) return;
        e.preventDefault();
        buttons[e.key === 'Home' ? 0 : e.key === 'End' ? buttons.length - 1 : Math.max(0, Math.min(buttons.length - 1, current + (e.key === 'ArrowDown' ? 1 : -1)))]?.focus();
      }}>
        {visibleItems.map(bot => {
          const m = bot.monitoring;
          const multipleEnvs = visibleItems.some(other => other.botId === bot.botId && other.ownerId === bot.ownerId && other.env !== bot.env);
          const label = !m ? '未监控' : m.status === 'PAUSED' ? '已暂停' : '监控中';
          const health = !m ? '' : ({ HEALTHY: '最近检查正常', ERROR: '最近检查异常', UNKNOWN: '检查状态未知', PAUSED: '监控已暂停' })[m.status];
          return <button type="button" key={bot.botRef} className={`bot-card ${selected?.botRef === bot.botRef ? 'selected' : ''}`} aria-pressed={selected?.botRef === bot.botRef}
            onClick={() => { callback.current(bot); setOpen(false); trigger.current?.focus(); }}>
            <span className="bot-glyph"><Icon name="bot" /></span><span className="candidate-content">
              <span className="candidate-heading"><strong title={bot.botName}>{bot.botName}</strong><span title={health} className={`enrollment-badge ${m?.status.toLowerCase() ?? 'unmonitored'}`}>{label}{m?.status === 'ERROR' ? ' !' : ''}</span></span>
              <span className="candidate-id mono" title={`${bot.botId} · ${bot.env}`}>{bot.botId}</span>
              <span className="candidate-owner">工号 {bot.ownerId}{multipleEnvs && <span className="candidate-env" aria-label={`环境 ${bot.env}`}>{bot.env}</span>}</span>
              <span className="candidate-summary">{m ? <><span title={m.unidentifiedSessionDiagnosisCount ? `另有 ${m.unidentifiedSessionDiagnosisCount} 条诊断缺少会话标识` : '按可靠会话标识去重，不代表扫描覆盖总量'}>已诊断会话 <b>{m.diagnosedSessionCount ?? '—'}</b>{m.unidentifiedSessionDiagnosisCount > 0 ? '*' : ''}</span><span>诊断 <b>{m.diagnosisCount}</b></span><span className={m.alertCount ? 'has-alert' : ''}>告警 <b>{m.alertCount}</b></span></> : '尚未加入监控，暂无监控数据'}</span>
            </span>
          </button>;
        })}
        {!loading && !error && loadedContext === context && draft.trim() === q && !visibleItems.length && <p className="empty-search">{nextCursor ? '当前批次暂无匹配 Bot，可继续加载。' : '未找到可查看的 Bot。'}</p>}
        {loadedContext === context && draft.trim() === q && nextCursor && <button type="button" className="load-bots" disabled={loading} onClick={() => setCursor(nextCursor)}>加载更多 Bot</button>}
      </div>
      {loading && <p role="status">正在加载 Bot…</p>}
      {error && <p role="alert">{error} <button type="button" onClick={() => setRetry(n => n + 1)}>重试</button></p>}
      <div className="picker-footer">{isAdmin ? '按所选范围展示 Bot' : '仅展示你拥有的 Bot'} · Esc 收起</div>
    </div>}
    {!open && !selected && !loading && !error && loadedContext === context && !items.length && <p className="empty-search">未找到可查看的 Bot。</p>}
    {!open && error && <p role="alert">{error} <button type="button" onClick={() => setRetry(n => n + 1)}>重试</button></p>}
  </div>;
}
