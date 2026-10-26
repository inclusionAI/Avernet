import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { sessionRecoveryApi } from '../../../../api/session-recovery';
import type { SessionRecoverySummary } from '../../../../types/session-recovery';
const statusLabels: Record<SessionRecoverySummary['status'], string> = {
  running: '执行中', waiting_owner: '等待方案确认', waiting_effect: '等待效果', completed: '已完成', failed: '执行失败', canceled: '已停止',
};
const statusClasses: Record<SessionRecoverySummary['status'], string> = {
  running: 'running', waiting_owner: 'unknown', waiting_effect: 'unknown', completed: '', failed: 'error', canceled: 'unknown',
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
  const task = loaded?.eventId === eventId ? loaded.items[0] : null;
  if (!task) return null;
  const ownerHandoff = task.completion_reason === 'owner_handoff' || task.completion_reason === 'mcp_permission_required';
  return <section className="recovery-task-links" aria-label="对应的自愈任务">
    <span className="recovery-task-label">自愈任务</span>
    <div className="recovery-task-item">
      <span className={`health ${statusClasses[task.status]}`}><span className="dot" />{ownerHandoff ? '需 Owner 自行处理' : task.completion_reason === 'target_unavailable' ? '目标不可用' : task.completion_reason === 'user_continued' ? '已让路' : statusLabels[task.status]}</span>
      <Link className="recovery-task-link" title={task.task_id} aria-label={`查看自愈任务 ${ownerHandoff ? '需 Owner 自行处理' : task.completion_reason === 'target_unavailable' ? '目标不可用' : task.completion_reason === 'user_continued' ? '已让路' : statusLabels[task.status]} ${task.task_id}`} to={`/insight?module=monitoring&recoveryTaskId=${encodeURIComponent(task.task_id)}`}>
        查看任务<span aria-hidden="true">↗</span>
      </Link>
    </div>
  </section>;
}
