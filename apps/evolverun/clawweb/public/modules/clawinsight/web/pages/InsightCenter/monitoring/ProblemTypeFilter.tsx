import { useEffect, useId, useRef, useState } from 'react';
import type { DiagnosisPage } from '../../../types/monitoring';
import { MonitoringIcon as Icon } from './MonitoringIcon';

type Props = {
  options: DiagnosisPage['problemTypes']; category: string; subtype: string;
  onApply: (category: string, subtype: string) => void;
};
export function ProblemTypeFilter({ options, category, subtype, onApply }: Props) {
  const [open, setOpen] = useState(false);
  const [draftCategory, setDraftCategory] = useState(category);
  const [draftSubtype, setDraftSubtype] = useState(subtype);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const first = useRef<HTMLSelectElement>(null);
  const id = useId();
  const close = () => { setOpen(false); trigger.current?.focus(); };
  useEffect(() => {
    if (!open) return;
    first.current?.focus();
    const dismiss = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); setOpen(false); trigger.current?.focus(); } };
    document.addEventListener('pointerdown', dismiss);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('keydown', escape); };
  }, [open]);
  const count = Number(Boolean(category)) + Number(Boolean(subtype));
  const subtypes = options.find(option => option.category === draftCategory)?.subtypes ?? [];
  const apply = (nextCategory: string, nextSubtype: string) => { onApply(nextCategory, nextSubtype); close(); };
  return <div className="business-filter-anchor" ref={root} onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false); }}>
    <button ref={trigger} type="button" className={`business-filter-trigger ${count ? 'selected' : ''}`} aria-expanded={open} aria-controls={id} aria-haspopup="dialog"
      title={category ? `${category}${subtype ? ` · ${subtype}` : ''}` : '按业务问题类型和子类型筛选'}
      onClick={() => { setDraftCategory(category); setDraftSubtype(subtype); setOpen(!open); }}>
      <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path strokeLinejoin="round" d="M3 4h18l-7 8v7l-4 2v-9L3 4Z" /></svg>
      <span>筛选问题类型</span>{count > 0 && <b title="已生效的筛选条件数量" aria-label={`已生效 ${count} 个筛选条件`}>{count}</b>}<Icon name="chevron" />
    </button>
    {open && <div id={id} role="dialog" aria-label="按问题类型筛选" className="business-filter-popover">
      <div className="filter-heading"><strong>按问题类型筛选</strong><button type="button" aria-label="关闭筛选面板" onClick={close}><Icon name="x" /></button></div>
      <label>业务问题类型<select ref={first} value={draftCategory} onChange={e => { setDraftCategory(e.target.value); setDraftSubtype(''); }}>
        <option value="">全部类型</option>{draftCategory && !options.some(o => o.category === draftCategory) && <option value={draftCategory}>{draftCategory}</option>}
        {options.map(o => <option key={o.category} value={o.category}>{o.category}</option>)}
      </select></label>
      <label>业务问题子类型<select value={draftSubtype} disabled={!draftCategory} onChange={e => setDraftSubtype(e.target.value)}>
        <option value="">{draftCategory ? '全部子类型' : '请先选择业务问题类型'}</option>
        {draftSubtype && !subtypes.includes(draftSubtype) && <option value={draftSubtype}>{draftSubtype}</option>}
        {subtypes.map(s => <option key={s} value={s}>{s}</option>)}
      </select></label>
      <div className="filter-actions"><button type="button" onClick={() => apply('', '')}>重置</button><button type="button" className="apply" onClick={() => apply(draftCategory, draftSubtype)}>应用筛选</button></div>
    </div>}
  </div>;
}
