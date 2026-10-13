import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { sessionRecoveryApi } from '../../../../api/session-recovery';
import type { SessionRecoverySummary } from '../../../../types/session-recovery';
export function RecoveryTaskLinks({ eventId }: { eventId: string }) {
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<{ eventId: string; items: SessionRecoverySummary[] } | null>(null);
  useEffect(() => {
    setError(null);
    const controller = new AbortController();
    void sessionRecoveryApi.list(eventId, controller.signal).then(value => {
      if (!controller.signal.aborted) setLoaded({ eventId, items: value.items });
    }).catch(() => { if (!controller.signal.aborted) setError(eventId); });
    return () => controller.abort();
  }, [eventId]);
  if (error === eventId) return <p role="status">自愈任务暂时无法读取，请稍后重新展开。</p>;
  if (loaded?.eventId !== eventId || !loaded.items.length) return null;
  return <div className="recovery-task-links"><strong>自愈任务</strong>{loaded.items.map(task =>
    <Link key={task.task_id} to={`/insight?module=monitoring&recoveryTaskId=${encodeURIComponent(task.task_id)}`}>查看任务时间线</Link>)}</div>;
}
