import { useEffect, useRef, useState } from 'react';
import type { BotOption } from '../../../types/monitoring';
import { monitoringApi, monitoringErrorCode, monitoringErrorText } from '../../../api/monitoring';
import { MonitoringIcon as Icon } from './MonitoringIcon';

export function MonitoringEnrollment({ bot, onRefresh }: { bot: BotOption; onRefresh: () => void }) {
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState(false), [error, setError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null), join = useRef<HTMLButtonElement>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => { request.current?.abort(); request.current = null; }, [bot.botRef]);
  useEffect(() => {
    if (!notice) return;
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, [notice]);
  const close = () => { setNotice(false); join.current?.focus(); };
  const enroll = async () => {
    const controller = new AbortController(); request.current?.abort(); request.current = controller;
    const timeout = setTimeout(() => controller.abort(new DOMException('Timeout', 'TimeoutError')), 15000);
    setBusy(true); setError('');
    try {
      await monitoringApi.enroll(bot.botRef, controller.signal);
      if (request.current === controller && !controller.signal.aborted) onRefresh();
    }
    catch (e) {
      if (request.current !== controller || (controller.signal.aborted && controller.signal.reason?.name !== 'TimeoutError')) return;
      const code = monitoringErrorCode(e), status = (e as { status?: number }).status;
      if (status === 501 && code === 'MONITORING_ENROLLMENT_NOT_IMPLEMENTED') setNotice(true);
      else if (status === 409 && code === 'MONITORING_ALREADY_ENROLLED') { setError('此 Bot 已加入监控，正在刷新。'); onRefresh(); }
      else { setError(monitoringErrorText(controller.signal.reason ?? e)); if ([401, 403, 404].includes(status ?? 0)) onRefresh(); }
    } finally { clearTimeout(timeout); if (request.current === controller) setBusy(false); }
  };
  return <section className="enrollment-panel" aria-label="未监控 Bot">
    <span className="enrollment-art"><Icon name="bot" /></span>
    <h2>此 Bot 尚未加入监控</h2><p>加入监控后，可在这里查看会话诊断与告警。</p>
    <p className="enrollment-identity">{bot.botName} · {bot.botId} · {bot.ownerId}</p>
    <button type="button" className="enroll-primary" ref={join} disabled={busy} onClick={() => void enroll()}>{busy ? '请稍候…' : '加入监控'}</button>
    {error && <p role="alert">{error}</p>}
    {notice && <dialog ref={dialog} className="join-dialog" aria-labelledby="monitoring-join-title" onCancel={e => { e.preventDefault(); close(); }}>
      <h2 id="monitoring-join-title">加入监控暂未开放</h2><p>加入监控功能正在开发中，敬请期待。</p>
      <div className="join-actions"><button type="button" autoFocus onClick={close}>知道了</button></div>
    </dialog>}
  </section>;
}
