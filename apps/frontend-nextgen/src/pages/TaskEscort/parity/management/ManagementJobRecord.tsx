import {
  managementJobKinds,
  managementJobLink,
  managementJobStatus,
  managementReasons,
  managementTime,
} from '@/domain/taskEscortParity/management';
import type { ManagementJob } from '@/domain/taskEscortParity/managementTypes';
import { DisabledWrite, Fields, yesNo } from './ManagementPrimitives';

export function ManagementJobRecord({
  workflowId,
  job,
  retryable,
}: {
  workflowId: string;
  job: ManagementJob;
  retryable: boolean;
}) {
  const link = managementJobLink(workflowId, job);
  return (
    <>
      <p>
        {managementJobKinds[job.kind] ?? job.kind} · <span>{managementJobStatus(job)}</span>
      </p>
      <p className="text-muted-foreground">{managementTime(job.createdAtMs)}</p>
      {job.reason && <p>{managementReasons[job.reason] ?? '请核对当前策略、负责人和原任务状态'}</p>}
      {link && (
        <a className="inline-block break-all text-primary underline underline-offset-4" href={link}>
          {job.flowId ? '查看分析' : '查看原优化任务'}
        </a>
      )}
      {retryable && job.status === 'blocked' && (
        <DisabledWrite>
          {job.kind === 'auto_run' ? '重试分析' : job.kind === 'auto_draft' ? '重试生成' : '重发通知'}
        </DisabledWrite>
      )}
      <details>
        <summary className="cursor-pointer text-primary">查看详情</summary>
        <div className="mt-3 space-y-3">
          <Fields
            fields={[
              ['任务编号', job.id],
              ['类型', job.kind],
              ['状态', job.status],
              ['尝试次数', job.attempts],
              ['原因代码', job.reason],
              ['运行编号', job.flowId],
              ['分析编号', job.analysisId],
              ['优化任务编号', job.taskId],
              ['证据完整', yesNo(job.complete)],
            ]}
          />
          {!link && <p className="text-muted-foreground">该记录未关联可跳转的运行或优化任务。</p>}
        </div>
      </details>
    </>
  );
}
