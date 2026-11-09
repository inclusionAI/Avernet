import { useEffect, useState } from 'react';
import { monitoringErrorText } from '../../../../api/monitoring';
import { sessionRecoveryApi } from '../../../../api/session-recovery';
import type { SessionRecoveryTask } from '../../../../types/session-recovery';
import { PlanMarkdown } from './PlanMarkdown';

function notificationLabel(task: SessionRecoveryTask): string {
  const event = task.timeline.filter(item => item.kind === 'notification' && item.payload.channel === 'owner_card'
    && item.payload.kind === 'owner_request' && item.payload.owner_request_id === task.ownerRequest?.requestId)
    .sort((a, b) => a.at_ms - b.at_ms).at(-1);
  if (event?.payload.state === 'sent') return 'Owner 通知：已发送';
  if (event?.payload.state === 'sending') return 'Owner 通知：正在发送';
  if (event?.payload.state === 'disabled') return 'Owner 通知：已关闭';
  return 'Owner 通知：发送未确认';
}

export function OwnerCooperation({ task, busy, onReload }: { task: SessionRecoveryTask; busy: boolean; onReload: () => Promise<void> }) {
  const [feedback, setFeedback] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const request = task.ownerRequest;
  useEffect(() => { setFeedback(''); }, [request?.requestId]);
  if (!request) return null;
  const isConfiguration = request.reason === 'configuration';
  const terminal = ['completed', 'failed', 'canceled'].includes(task.status);
  const pending = !terminal && task.status === 'waiting_owner' && request.status === 'pending';
  const status = terminal ? '配合处理已结束' : request.status === 'confirmed' ? '已反馈，正在核验' : '等待 Owner 配合';

  const confirm = async () => {
    if (!task.canOperate || !pending || busy || submitting) return;
    setSubmitting(true);
    setError('');
    let saved = false;
    try {
      await sessionRecoveryApi.confirmOwnerRequest(task.taskId, request.requestId, feedback);
      saved = true;
      await onReload();
    } catch (failure) {
      const statusCode = (failure as { status?: number } | null)?.status;
      let message = saved ? '反馈已保存，任务刷新失败，请刷新任务查看核验进展。'
        : statusCode === 403 ? '当前账号无法确认此事项，请核对任务 Owner 或管理员权限。'
        : statusCode === 409 ? '处理事项已更新或结束，请核对当前任务后再操作。' : monitoringErrorText(failure);
      if (!saved && (statusCode === 403 || statusCode === 409)) {
        try { await onReload(); } catch { message += ' 任务刷新失败，请刷新任务后重试。'; }
      }
      setError(message);
    } finally { setSubmitting(false); }
  };

  return <section className="sr-card sr-owner-action" aria-label="Owner 配合">
    <div className="sr-card-header"><div><h2>Owner 配合</h2><p>{isConfiguration ? '需要补齐配置' : '需要权限授权'}</p></div><span className={`sr-pill ${pending ? 'warning' : terminal ? 'neutral' : 'running'}`}>{status}</span></div>
    <div className="sr-plan-body">
      <PlanMarkdown text={request.summary} />
      <div className="sr-owner-request sr-owner-item"><PlanMarkdown text={request.instructions} /></div>
      <p className="sr-note">{notificationLabel(task)}</p>
      {request.evidence_ids.length > 0 && <details className="sr-disclosure"><summary>查看配合事项的依据</summary>{task.evidence.filter(item => request.evidence_ids.includes(item.evidence_id)).map(item => <div className="sr-note-box" key={item.evidence_id}><p>{item.excerpt}</p></div>)}</details>}
      {request.feedback && <div className="sr-note-box"><strong>Owner 处理反馈</strong><p>{request.feedback.text || 'Owner 已确认处理。'}</p></div>}
      {!terminal && <p className="sr-note">{isConfiguration ? '确认处理后，Agent 将核验配置和原任务进展，再决定是否续接。' : '确认处理后，Agent 将重新核验权限，并继续原任务；恢复效果以核验结果为准。'}</p>}
      {error && <p role="alert" className="sr-process-error">{error}</p>}
      {!task.simulation && task.canOperate && pending && <div className="sr-decision-panel">
        <label>处理说明（可选）<textarea maxLength={4000} value={feedback} disabled={busy || submitting} onChange={event => setFeedback(event.target.value)} placeholder={isConfiguration ? '填写已补齐的配置，供 Agent 核验' : '填写已完成的权限处理，供 Agent 核验'} /></label>
        <div className="sr-actions"><button type="button" className="sr-button primary" disabled={busy || submitting} onClick={() => void confirm()}>{submitting ? '提交中…' : '已处理，继续核验'}</button></div>
      </div>}
    </div>
  </section>;
}
