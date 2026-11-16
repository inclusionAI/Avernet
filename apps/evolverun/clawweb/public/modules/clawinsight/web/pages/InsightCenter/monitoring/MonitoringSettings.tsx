import { useEffect, useRef, useState } from 'react';
import { monitoringApi, monitoringErrorText } from '../../../api/monitoring';
import type { BotOptionsPage } from '../../../types/monitoring';

export function MonitoringSettings({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [q, setQ] = useState('');
  const [search, setSearch] = useState('');
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const [page, setPage] = useState<BotOptionsPage | null>(null);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const cursor = cursors[cursors.length - 1];
  useEffect(() => { const el = dialog.current; el?.showModal(); return () => el?.close(); }, []);
  useEffect(() => { const timer = setTimeout(() => { setSearch(q.trim()); setCursors([null]); }, 250); return () => clearTimeout(timer); }, [q]);
  useEffect(() => {
    const controller = new AbortController(); let active = true; setPage(null); setError('');
    const timer = setTimeout(() => controller.abort(new DOMException('Timeout', 'TimeoutError')), 15000);
    monitoringApi.options('mine', search, { startDate: '', endDate: '' }, cursor, controller.signal)
      .then(value => { if (!controller.signal.aborted) setPage(value); })
      .catch(failure => { if (active) setError(monitoringErrorText(controller.signal.reason ?? failure)); })
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
    <div className="settings-heading"><h2 id="monitoring-settings-title">诊断监控设置</h2><button type="button" disabled={busy} aria-label="关闭设置" onClick={onClose}>关闭</button></div>
    <p>开启后，为 Bot 启用诊断监控和自动自愈。关闭后，已开始的自愈任务会继续处理。</p>
    <div className="settings-toolbar"><input type="search" aria-label="搜索我的 Bot" placeholder="搜索 Bot 名称或 ID" value={q} disabled={busy} onChange={event => setQ(event.target.value)} />
      <button type="button" disabled={busy} onClick={() => void save({ all: true, enabled: true })}>一键全开</button>
      <button type="button" disabled={busy} onClick={() => void save({ all: true, enabled: false })}>一键全关</button></div>
    <p className="settings-hint">批量操作适用于当前账号已有的全部 Bot，包含其他分页和未命中搜索的 Bot。</p>
    {error && <p role="alert" className="error-banner">{error}<button type="button" onClick={() => setRevision(value => value + 1)}>重试</button></p>}
    {message && <p role="status">{message}</p>}
    {!page && !error && <p role="status">正在加载…</p>}
    {page?.items.length === 0 && <p>没有找到 Bot。</p>}
    <ul className="settings-bots">{page?.items.map(bot => {
      const config = bot.configuration, enabled = config?.diagnosisEnabled === true && config.recoveryEnabled === true;
      const partial = Boolean(config?.diagnosisEnabled) !== Boolean(config?.recoveryEnabled);
      return <li key={bot.botRef}><div><strong>{bot.botName}</strong><small>{bot.botId} · {bot.ownerId} · {bot.env}</small></div>
        <span>{partial ? '部分开启' : enabled ? '已开启' : '已关闭'}</span>
        <button type="button" role="switch" aria-label={`${bot.botName} 诊断监控`} aria-checked={enabled} className="monitoring-switch" disabled={busy}
          onClick={() => void save({ botRef: bot.botRef, version: config?.version ?? 0, enabled: !enabled })}><span /></button></li>;
    })}</ul>
    <div className="settings-toolbar"><button type="button" disabled={busy || cursors.length === 1} onClick={() => setCursors(value => value.slice(0, -1))}>上一页</button>
      <span>第 {cursors.length} 页</span><button type="button" disabled={busy || !page?.nextCursor} onClick={() => setCursors(value => [...value, page!.nextCursor])}>下一页</button></div>
  </dialog>;
}
