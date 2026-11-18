import { useEffect, useRef, useState } from 'react';
import { monitoringApi, monitoringErrorText } from '../../../api/monitoring';
import { MonitoringIcon as Icon } from './MonitoringIcon';
import type { BotOptionsPage } from '../../../types/monitoring';

export function MonitoringSettings({ onClose, onChanged, practiceUrl }: { onClose: () => void; onChanged: () => void; practiceUrl?: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [q, setQ] = useState('');
  const [search, setSearch] = useState('');
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const [page, setPage] = useState<BotOptionsPage | null>(null);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [message, setMessage] = useState('');
  const cursor = cursors[cursors.length - 1];
  useEffect(() => { const el = dialog.current; el?.showModal(); return () => el?.close(); }, []);
  useEffect(() => { const timer = setTimeout(() => { setSearch(q.trim()); setCursors([null]); }, 250); return () => clearTimeout(timer); }, [q]);
  useEffect(() => {
    const controller = new AbortController(); let active = true; setPage(null); setLoadError('');
    const timer = setTimeout(() => controller.abort(new DOMException('Timeout', 'TimeoutError')), 15000);
    monitoringApi.options('mine', search, { startDate: '', endDate: '' }, cursor, controller.signal)
      .then(value => { if (!controller.signal.aborted) setPage(value); })
      .catch(failure => { if (active) setLoadError(monitoringErrorText(controller.signal.reason ?? failure)); })
      .finally(() => clearTimeout(timer));
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [search, cursor, revision]);
  async function save(input: Parameters<typeof monitoringApi.configure>[0]) {
    setBusy(true); setError(''); setMessage('');
    try {
      const result = await monitoringApi.configure(input);
      setMessage(result.failed.length ? `已更新 ${result.updated} 个 Bot，${result.failed.length} 个未成功，请刷新后重试。` : `已更新 ${result.updated} 个 Bot。`);
      setRevision(value => value + 1); onChanged();
    } catch (failure) { setError((failure as { status?: number }).status === 409 ? '配置已被更新，请刷新后重试。' : monitoringErrorText(failure)); setRevision(value => value + 1); }
    finally { setBusy(false); }
  }
  return <dialog ref={dialog} className="monitoring-settings" aria-labelledby="monitoring-settings-title" onCancel={event => { if (busy) event.preventDefault(); else onClose(); }}>
    <header className="settings-heading">
      <h2 id="monitoring-settings-title">诊断监控设置</h2>
      <button type="button" className="settings-close" disabled={busy} aria-label="关闭设置" onClick={onClose}><Icon name="x" /></button>
    </header>
    <div className="settings-notice settings-overview">
      <Icon name="pulse" />
      <div>
        <p className="settings-description">诊断自愈会监测 Bot 的会话。发现任务卡住或执行异常时，系统会分析原因，并尝试让原任务继续执行。</p>
        {practiceUrl && <a className="settings-case-link" href={practiceUrl} target="_blank" rel="noopener noreferrer">
          查看诊断自愈实践案例 <span aria-hidden="true">↗</span>
        </a>}
      </div>
    </div>
    <div className="settings-notice settings-operation-notice"><Icon name="text" /><span>用 Bot 旁的开关可单独开启或关闭，也可以点击「一键全开」或「一键全关」批量设置。</span></div>
    <div className="settings-notice settings-service-notice"><Icon name="alert" /><span>服务 Bot 配置请联系山宗、安远、沐荛。</span></div>
    <div className="records-toolbar settings-toolbar"><label className="search-box"><Icon name="search" /><input type="search" aria-label="搜索我的 Bot" placeholder="搜索 Bot 名称或 ID" value={q} disabled={busy} onChange={event => setQ(event.target.value)} /></label>
      <div className="settings-actions" role="group" aria-label="批量设置诊断自愈">
        <button type="button" className="copy-btn" disabled={busy} onClick={() => void save({ all: true, enabled: false })}>一键全关</button>
        <button type="button" className="copy-btn settings-primary" disabled={busy} onClick={() => void save({ all: true, enabled: true })}>一键全开</button>
      </div></div>
    <p className="settings-hint">批量操作覆盖名下所有可自行设置的 Bot，包含当前搜索和分页之外的 Bot。</p>
    {(error || loadError) && <p role="alert" className="error-banner">{error || loadError}<button type="button" disabled={busy} onClick={() => { setError(''); setRevision(value => value + 1); }}>刷新</button></p>}
    {message && <p role="status" className="settings-feedback">{message}</p>}
    {!page && !loadError && <p className="monitoring-empty" role="status">正在加载…</p>}
    {page?.items.length === 0 && <p className="monitoring-empty">没有找到 Bot。</p>}
    <ul className="settings-bots">{page?.items.map(bot => {
      const config = bot.configuration, enabled = config?.diagnosisEnabled === true && config.recoveryEnabled === true;
      return <li key={bot.botRef} className={bot.isServiceBot ? 'settings-service-bot' : undefined}><span className="bot-glyph"><Icon name="bot" /></span>
        <div className="record-text"><strong className="record-title">{bot.botName}</strong><small className="record-session mono">{bot.botId} · {bot.ownerId} · {bot.env}</small>
          {bot.isServiceBot && <span className="settings-bot-note">服务 Bot 请联系平台</span>}</div>
        <span className={`settings-state${enabled && !bot.isServiceBot ? ' enabled' : ''}`}>{enabled ? '已开启' : '已关闭'}</span>
        <button type="button" role="switch" aria-label={`${bot.botName} 诊断监控`} aria-checked={enabled} className="monitoring-switch" disabled={busy || bot.isServiceBot}
          onClick={() => void save({ botRef: bot.botRef, version: config?.version ?? 0, enabled: !enabled })}><span /></button></li>;
    })}</ul>
    <footer className="settings-footer"><button type="button" className="copy-btn" disabled={busy || cursors.length === 1} onClick={() => setCursors(value => value.slice(0, -1))}>上一页</button>
      <span>第 {cursors.length} 页</span><button type="button" className="copy-btn" disabled={busy || !page?.nextCursor} onClick={() => setCursors(value => [...value, page!.nextCursor])}>下一页</button></footer>
  </dialog>;
}
