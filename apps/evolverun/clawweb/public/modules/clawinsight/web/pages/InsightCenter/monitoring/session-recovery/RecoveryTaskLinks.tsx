import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { sessionRecoveryApi } from '../../../../api/session-recovery';
import type { SessionRecoverySummary } from '../../../../types/session-recovery';
const statusLabels: Record<SessionRecoverySummary['status'], string> = {
  running: '执行中', waiting_owner: '等待方案确认', waiting_effect: '等待效果', completed: '已完成', failed: '执行失败',
};
export function RecoveryTaskLinks({ eventId }: { eventId: string }) {
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<{ eventId: string; items: SessionRecoverySummary[] } | null>(null);
  useEffect(() => {
    setError(null);
    const controller = new AbortController();
    void sessionRecoveryApi.list(eventId, controller.signal).then(value => {
      if (!controller.signal.aborted) setLoaded({ eventId, items: value.items });
    }).catch(() => { if (!controller.signal.aborted) setError(eventId); });
    return () => controller.abort();
  }, [eventId, revision]);
  if (error === eventId) return <div className="recovery-task-links" role="status">自愈任务暂时无法读取。<button type="button" className="copy-btn" onClick={() => setRevision(value => value + 1)}>重试</button></div>;
  if (loaded?.eventId !== eventId || !loaded.items.length) return null;
  return <section className="recovery-task-links" aria-label="对应的自愈任务"><strong>自愈任务</strong>{loaded.items.map(task =>
    <Link key={task.task_id} className="recovery-task-link" to={`/insight?module=monitoring&recoveryTaskId=${encodeURIComponent(task.task_id)}`}>
      <span>查看自愈任务</span><span>{statusLabels[task.status]}</span><code>{task.task_id}</code>
    </Link>)}</section>;
}
