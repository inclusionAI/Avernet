import { useId, useState } from 'react';
import type { DiagnosisItem } from '../../../types/monitoring';
import { MonitoringIcon as Icon } from './MonitoringIcon';

export const decisionLabels = { ALL: '全部', ALERT: '告警', PASS: '通过', UNRESOLVED: '无法判断' };
const timeFormat = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
export function timeParts(value: string | null) {
  if (!value || !Number.isFinite(Date.parse(value))) return null;
  const parts = Object.fromEntries(timeFormat.formatToParts(new Date(value)).map(p => [p.type, p.value]));
  return { day: `${parts.month}-${parts.day}`, date: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}:${parts.second}` };
}
export function displayTime(value: string | null): string {
  const p = timeParts(value);
  return p ? `${p.date} ${p.time}（北京时间）` : '时间未知';
}
function Field({ label, value, emphasis = false }: { label: string; value: string | null; emphasis?: boolean }) {
  return <div><dt>{label}</dt><dd className={emphasis ? 'confidence' : undefined}>{value ?? '—'}</dd></div>;
}
export function DiagnosisRecord({ item, open, toggle }: { item: DiagnosisItem; open: boolean; toggle: () => void }) {
  const detailId = useId();
  const [copyMessage, setCopyMessage] = useState('');
  const title = [item.businessProblemCategory, item.businessProblemSubtype].filter(Boolean).join(' · ') || decisionLabels[item.decision];
  const identifier = item.traceId || item.sessionKey || item.sessionId || item.diagnosisId;
  const time = timeParts(item.occurredAt);
  const symbol = { ALERT: 'alert', PASS: 'check', UNRESOLVED: 'question' } as const;
  const ids = [['诊断编号', item.diagnosisId], ['Session Key', item.sessionKey], ['Session ID', item.sessionId], ['Trace ID', item.traceId]];
  const copy = async (label: string, value: string) => {
    try { await navigator.clipboard.writeText(value); setCopyMessage(`${label}已复制`); }
    catch { setCopyMessage('未能访问剪贴板，请选择并复制上方标识。'); }
  };
  return <article className={`record ${open ? 'expanded' : ''}`}>
    <button type="button" onClick={toggle} aria-expanded={open} aria-controls={detailId} className="record-summary">
      <span className="record-main">
        <span className={`decision-symbol ${item.decision.toLowerCase()}`}><Icon name={symbol[item.decision]} /></span>
        <span className="record-text"><span className="record-title-line"><span className="record-title" title={title}>{title}</span><span className="decision-word">{decisionLabels[item.decision]}</span></span><span className="record-session mono" title={identifier}>{identifier}</span></span>
      </span>
      <span className="record-time" title={displayTime(item.occurredAt)}>{time ? <><span className="day">{time.day}</span><span>{time.time}</span></> : '时间未知'}</span>
      <span className={`intervention ${item.humanIntervention ? 'yes' : ''}`} aria-label={`人工干预：${item.humanIntervention ? '是' : '否'}`}>{item.humanIntervention && <Icon name="user" />}{item.humanIntervention ? '是' : '否'}</span>
      <Icon name="chevron" className="expand-icon" />
    </button>
    {open && <div id={detailId} className="record-detail">
      <div className="diagnosis-narratives">{[['业务诊断', item.businessDiagnosis, 'text'], ['系统诊断', item.systemDiagnosis, 'code']].map(([label, text, icon]) => <section key={label} className="narrative"><h3 className="narrative-label"><Icon name={icon as 'text' | 'code'} />{label}</h3><p>{text ?? '—'}</p></section>)}</div>
      <dl className="detail-grid">
        <Field label="TC 故障标签" value={item.tcFaultLabel} /><Field label="诊断置信度" value={item.confidence === null ? null : `${(item.confidence * 100).toFixed(1)}%`} emphasis />
        <Field label="业务问题类型" value={item.businessProblemCategory} /><Field label="业务问题子类型" value={item.businessProblemSubtype} />
        <Field label="处理人" value={item.handlerName} /><Field label="是否人工干预" value={item.humanIntervention ? '是' : '否'} />
        <Field label="会话时间" value={displayTime(item.occurredAt)} /><Field label="诊断完成时间" value={displayTime(item.diagnosedAt)} />
      </dl>
      <div className="detail-identifiers">{ids.map(([label, value]) => value && <div className="id-line" key={label}><span>{label}</span><code>{value}</code><button className="copy-btn" type="button" aria-label={`复制${label}`} onClick={() => void copy(label!, value)}><Icon name="copy" /></button></div>)}</div>
      <p className="copy-message" role="status">{copyMessage}</p>
    </div>}
  </article>;
}
