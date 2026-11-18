import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import type {
  ManagementJob,
  ManagementRecordKind,
  ManagementRecordPeriod,
  ManagementRecordQuery,
  ManagementRecordStatus,
} from '@/domain/taskEscortParity/managementTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortManagementResource } from '@/hooks/taskEscortParity/useTaskEscortManagement';
import { managementService } from '@/services/taskEscortParity/managementService';
import { useCallback, useState } from 'react';
import { ManagementJobRecord } from './ManagementJobRecord';
import { ReadState } from './ManagementPrimitives';

const periods: [ManagementRecordPeriod, string][] = [
  ['all', '全部时间'],
  ['today', '今天'],
  ['7d', '近 7 天'],
];
const statuses: [ManagementRecordStatus, string][] = [
  ['all', '全部状态'],
  ['pending', '待处理'],
  ['dispatching', '正在提交'],
  ['waiting', '等待结果'],
  ['retryable', '等待重试'],
  ['uncertain', '结果待核对'],
  ['done', '已结束'],
  ['blocked', '需要处理'],
];
export function ManagementAutomationHistory({
  context,
  kind,
  retryableJobs,
}: {
  context: TaskEscortParityContext;
  kind: ManagementRecordKind;
  retryableJobs: ManagementJob[] | null;
}) {
  const [period, setPeriod] = useState<ManagementRecordPeriod>('all');
  const [status, setStatus] = useState<ManagementRecordStatus>(kind === 'notification' ? 'attention' : 'all');
  const [cursors, setCursors] = useState<string[]>(['']);
  const cursor = cursors[cursors.length - 1];
  const read = useCallback(
    (signal: AbortSignal) => {
      const query: ManagementRecordQuery = {
        kind,
        period,
        status,
        limit: kind === 'notification' ? 8 : 20,
        ...(cursor ? { cursor } : {}),
      };
      return managementService.records(context.workflowId, context.userId!, query, signal);
    },
    [context.workflowId, context.userId, kind, period, status, cursor],
  );
  const resource = useTaskEscortManagementResource(context, `records:${kind}:${period}:${status}:${cursor}`, read);
  const label = kind === 'analysis' ? '自动分析记录' : kind === 'optimization' ? '自动优化记录' : '通知发送异常';
  const data = resource.data;
  return (
    <section aria-label={label} aria-busy={resource.loading} className="space-y-3 border-t border-border pt-4">
      <h4 className="font-medium">{label}</h4>
      <div className="flex flex-wrap items-center gap-3">
        <div className="w-full sm:w-40">
          <Select
            value={period}
            onValueChange={(value) => {
              setPeriod(value as ManagementRecordPeriod);
              setCursors(['']);
            }}
          >
            <SelectTrigger aria-label="执行记录时间范围">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {periods.map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="w-full sm:w-40">
          <Select
            value={status}
            onValueChange={(value) => {
              setStatus(value as ManagementRecordStatus);
              setCursors(['']);
            }}
          >
            <SelectTrigger aria-label="执行记录状态">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {[...statuses, ...(kind === 'notification' ? [['attention', '通知异常'] as const] : [])].map(
                ([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ),
              )}
            </SelectContent>
          </Select>
        </div>
        <Button variant="outline" size="sm" disabled={resource.loading} onClick={resource.refresh}>
          刷新记录
        </Button>
      </div>
      <p className="text-muted-foreground">
        按创建时间倒序，上海时间。{kind === 'optimization' && '仅含自动生成草稿的任务。'}
        {kind === 'notification' && '默认仅展示未送达或结果待核对的通知。'}
      </p>
      <ReadState resource={resource} />
      {data && (
        <>
          {!data.jobs.length && (
            <Empty
              compact
              title={
                period !== 'all' || !['all', 'attention'].includes(status)
                  ? '没有符合筛选条件的记录'
                  : kind === 'notification'
                  ? '暂无通知发送异常'
                  : `暂无${label}`
              }
            />
          )}
          <ul className="max-h-96 space-y-3 overflow-y-auto">
            {data.jobs.map((job) => (
              <li key={job.id} className="space-y-2 rounded-lg border border-border p-3">
                <ManagementJobRecord
                  workflowId={context.workflowId}
                  job={job}
                  retryable={retryableJobs?.some((candidate) => candidate.id === job.id) === true}
                />
              </li>
            ))}
          </ul>
          <nav aria-label="执行记录分页" className="flex flex-wrap items-center justify-between gap-3">
            <Button
              variant="outline"
              size="sm"
              disabled={cursors.length === 1 || resource.loading}
              onClick={() => setCursors((current) => current.slice(0, -1))}
            >
              上一页
            </Button>
            <span>
              第 {cursors.length} 页 · 每页最多 {kind === 'notification' ? 8 : 20} 条
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={!data.nextCursor || resource.loading}
              onClick={() => {
                if (data.nextCursor) setCursors((current) => [...current, data.nextCursor!]);
              }}
            >
              下一页
            </Button>
          </nav>
        </>
      )}
    </section>
  );
}
