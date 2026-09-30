import { useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { BotOption } from '../../../types/monitoring';
import { MonitoringBotSelector } from './MonitoringBotSelector';
import { MonitoringIcon as Icon } from './MonitoringIcon';

/** Calendar dates are always Beijing dates, independently of the browser's timezone. */
export function beijingDateRange(days: number, now = new Date()) {
  const endDate = new Date(now.getTime() + 8 * 3600000).toISOString().slice(0, 10);
  const startDate = new Date(Date.parse(`${endDate}T00:00:00Z`) - (days - 1) * 86400000).toISOString().slice(0, 10);
  return { startDate, endDate };
}

type Props = {
  selected: BotOption | null; isAdmin: boolean; revision: number; loading: boolean;
  startDate: string; endDate: string;
  onBotChange: (bot: BotOption) => void;
  onDatesChange: (range: { startDate: string; endDate: string }) => void;
  onUnavailable: (error: unknown) => void; onRefresh: () => void; children: ReactNode;
};
export function MonitoringControls(props: Props) {
  const { selected, isAdmin, revision, loading, startDate, endDate, onBotChange, onDatesChange, onRefresh, children } = props;
  const [menu, setMenu] = useState<'date' | null>(null);
  const [draft, setDraft] = useState({ startDate, endDate });
  const root = useRef<HTMLElement>(null);
  const dateButton = useRef<HTMLButtonElement>(null);
  const dateMenu = useRef<HTMLDivElement>(null);
  const id = useId();
  const invalidDates = Boolean(draft.startDate && draft.endDate && draft.startDate > draft.endDate);
  useEffect(() => {
    if (!menu) return;
    const trigger = dateButton.current;
    dateMenu.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const dismiss = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setMenu(null); };
    const keyboard = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.preventDefault(); setMenu(null); trigger?.focus(); } };
    document.addEventListener('pointerdown', dismiss);
    document.addEventListener('keydown', keyboard);
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('keydown', keyboard); };
  }, [menu]);
  const apply = (range: typeof draft) => { onDatesChange(range); setMenu(null); dateButton.current?.focus(); };
  const dateLabel = startDate || endDate
    ? startDate === endDate ? startDate : `${startDate || '不限起始'} — ${endDate || '至今'}`
    : '全部时间';
  return <section className="monitor-context" aria-label="监控筛选" ref={root}>
    <div className="control-row">
      <MonitoringBotSelector selected={selected} isAdmin={isAdmin} revision={revision} startDate={startDate} endDate={endDate} onSelect={onBotChange} onUnavailable={props.onUnavailable} />
      <div className="control-block date-control">
        <span className="label">会话时间</span>
        <button ref={dateButton} type="button" className="control-button" aria-label="选择会话时间范围" aria-expanded={menu === 'date'} aria-controls={`${id}-dates`} onClick={() => { setDraft({ startDate, endDate }); setMenu(menu === 'date' ? null : 'date'); }}><Icon name="calendar" /><span className="date-label">{dateLabel}</span><Icon name="chevron" className="chevron" /></button>
        {menu === 'date' && <div className="popover date-menu" id={`${id}-dates`} ref={dateMenu}>
          <div className="date-presets" aria-label="快捷时间范围">
            <button type="button" onClick={() => apply({ startDate: '', endDate: '' })}>全部时间</button>
            {[[1, '今天'], [7, '近 7 天'], [30, '近 30 天']].map(([days, label]) => <button type="button" key={days} onClick={() => apply(beijingDateRange(Number(days)))}>{label}</button>)}
          </div>
          <div className="date-fields">
            <label>开始日期<input type="date" value={draft.startDate} aria-invalid={invalidDates} aria-describedby={invalidDates ? `${id}-date-error` : undefined} onInput={e => { const value = e.currentTarget.value; setDraft(current => ({ ...current, startDate: value })); }} /></label>
            <label>结束日期<input type="date" value={draft.endDate} aria-invalid={invalidDates} aria-describedby={invalidDates ? `${id}-date-error` : undefined} onInput={e => { const value = e.currentTarget.value; setDraft(current => ({ ...current, endDate: value })); }} /></label>
          </div>
          {invalidDates && <p id={`${id}-date-error`} role="alert" className="date-error">开始日期不能晚于结束日期，请调整时间范围。</p>}
          <div className="date-actions"><button type="button" onClick={() => { setMenu(null); dateButton.current?.focus(); }}>取消</button><button type="button" className="primary" disabled={invalidDates} onClick={() => apply(draft)}>应用</button></div>
        </div>}
      </div>
      <div className="refresh-area"><button type="button" className={`refresh ${loading ? 'busy' : ''}`} disabled={loading} onClick={onRefresh}><Icon name="refresh" /><span>{loading ? '刷新中…' : '刷新'}</span></button></div>
    </div>
    {children}
  </section>;
}
