import { Button } from '@/components/ui/Button';
import { Switch } from '@/components/ui/Switch';
import { MANAGEMENT_WRITE_REASON, managementReasons, managementTime } from '@/domain/taskEscortParity/management';
import type { ManagementRecordKind } from '@/domain/taskEscortParity/managementTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortManagementResource } from '@/hooks/taskEscortParity/useTaskEscortManagement';
import { managementService } from '@/services/taskEscortParity/managementService';
import { useCallback, useId, useState } from 'react';
import { ManagementAutomationHistory } from './ManagementAutomationHistory';
import { ManagementJobRecord } from './ManagementJobRecord';
import { DisabledWrite, Fields, ReadState, yesNo } from './ManagementPrimitives';

export function ManagementAutomation({ context }: { context: TaskEscortParityContext }) {
  const [history, setHistory] = useState<ManagementRecordKind | null>(null);
  const policyRead = useCallback(
    (signal: AbortSignal) => managementService.policy(context.workflowId, context.userId!, signal),
    [context.workflowId, context.userId],
  );
  const statusRead = useCallback(
    (signal: AbortSignal) => managementService.status(context.workflowId, context.userId!, signal),
    [context.workflowId, context.userId],
  );
  const policy = useTaskEscortManagementResource(context, 'automation:policy', policyRead);
  const status = useTaskEscortManagementResource(context, 'automation:status', statusRead, history !== null);
  const reasonId = useId(),
    historyId = useId();
  const p = policy.data;
  const historyButton = (kind: ManagementRecordKind, label: string) => (
    <Button
      variant="link"
      size="sm"
      aria-expanded={history === kind}
      aria-controls={`${historyId}-${kind}`}
      onClick={() => setHistory((current) => (current === kind ? null : kind))}
    >
      {history === kind ? '收起记录' : label}
    </Button>
  );
  const historyPanel = (kind: ManagementRecordKind) =>
    history === kind && (
      <div id={`${historyId}-${kind}`}>
        <ManagementAutomationHistory
          key={kind}
          context={context}
          kind={kind}
          retryableJobs={status.data?.retryableJobs ?? null}
        />
      </div>
    );
  return (
    <div className="space-y-4">
      <ReadState resource={policy} />
      {p && (
        <>
          <Fields
            fields={[
              [
                '自动任务执行环境',
                p.executionEnvironment === null ? '未记录' : p.executionEnvironment === 'pre' ? '预发' : '生产',
              ],
              ['策略来源', { database: '数据库', environment: '环境配置', default: '服务端默认配置' }[p.source]],
              ['配置版本', p.version],
              ['更新时间', managementTime(p.updatedAtMs)],
            ]}
          />
          <p id={reasonId} className="text-muted-foreground">
            {MANAGEMENT_WRITE_REASON}；所有设置仅显示服务端当前值，不会即时保存、触发分析、生成草稿或发送通知。
          </p>
          <section aria-label="自动分析设置" className="space-y-3 rounded-xl border border-border p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h3 className="text-sm font-medium">自动分析</h3>
              {historyButton('analysis', '查看自动分析记录')}
            </div>
            <div className="flex items-center gap-3">
              <Switch aria-label="自动分析" checked={p.enabled} disabled aria-describedby={reasonId} />
              <span>{p.enabled ? '已开启' : '已关闭'}</span>
            </div>
            <p className="text-muted-foreground">
              失败后分析问题并生成建议，不修改工作流。已有任务状态以真实记录为准。
            </p>
            <h4 className="font-medium">每日草稿计划</h4>
            <Fields
              fields={[
                ['计划状态', p.dailyConfigured ? (p.enabled ? '已配置并开启' : '已配置，当前暂停') : '尚未启用'],
                ['每日执行时间', p.dailyTime],
                ['时区', p.timezone],
                [
                  '最低影响',
                  p.minimumImpact === null ? '未记录' : p.minimumImpact === 'high' ? '高等及以上' : '中等及以上',
                ],
              ]}
            />
            <DisabledWrite>保存每日草稿计划</DisabledWrite>
            {historyPanel('analysis')}
          </section>
          <section aria-label="自动优化" className="space-y-3 rounded-xl border border-border p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h3 className="text-sm font-medium">自动优化</h3>
              {historyButton('optimization', '查看自动优化记录')}
            </div>
            <div className="flex items-center gap-3">
              <Switch aria-label="自动生成优化草稿" checked={p.autoDraft} disabled aria-describedby={reasonId} />
              <span>自动生成优化草稿：{yesNo(p.autoDraft)}</span>
            </div>
            <p className="text-muted-foreground">
              按每日计划从已有合格建议生成草稿，仍需人工审阅确认发布，不会自动部署。
              {!p.enabled && '自动分析已关闭；已有任务继续核对。'}
            </p>
            <DisabledWrite>保存自动优化设置</DisabledWrite>
            {historyPanel('optimization')}
          </section>
          <section aria-label="进化任务通知" className="space-y-3 rounded-xl border border-border p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h3 className="text-sm font-medium">进化任务通知</h3>
              {historyButton('notification', '查看通知异常')}
            </div>
            <Fields
              fields={[
                [
                  '通知人（工号）',
                  p.notificationRecipients === null
                    ? '未记录'
                    : p.notificationRecipients.length
                    ? p.notificationRecipients.join('、')
                    : '未指定（按服务端负责人策略）',
                ],
                ['通知配置版本', p.notificationVersion],
              ]}
            />
            <p className="text-muted-foreground">手动和自动优化任务完成或失败的通知，不依赖自动分析开关。</p>
            <DisabledWrite>保存通知人</DisabledWrite>
            {historyPanel('notification')}
          </section>
        </>
      )}
      {history !== null && (
        <section aria-label="自动化状态快照" className="space-y-3 rounded-xl border border-border bg-muted p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-sm font-medium">自动化状态快照</h3>
            <Button variant="outline" size="sm" disabled={status.loading} onClick={status.refresh}>
              刷新状态
            </Button>
          </div>
          <ReadState resource={status} />
          {status.data && (
            <>
              <Fields
                fields={[
                  ['最近扫描', managementTime(status.data.lastScannedAtMs)],
                  [
                    '暂停原因',
                    status.data.pauseReason
                      ? managementReasons[status.data.pauseReason] ?? status.data.pauseReason
                      : '未记录暂停原因',
                  ],
                  ['覆盖运行总数', status.data.coverage.total],
                  ['待分析', status.data.coverage.pending],
                  ['失败', status.data.coverage.failed],
                  ['部分覆盖', status.data.coverage.partial],
                  ['未知', status.data.coverage.unknown],
                  ['近期任务数', status.data.jobs.length],
                  ['可重试任务数', status.data.retryableJobs?.length ?? '未记录'],
                ]}
              />
              <details>
                <summary className="cursor-pointer text-primary">近期任务与可重试任务详情</summary>
                <div className="mt-3 space-y-3">
                  <h4 className="font-medium">近期任务</h4>
                  {status.data.jobs.length ? (
                    <ul className="space-y-3">
                      {status.data.jobs.map((job) => (
                        <li key={job.id} className="space-y-2 rounded-lg border border-border p-3">
                          <ManagementJobRecord
                            workflowId={context.workflowId}
                            job={job}
                            retryable={
                              status.data?.retryableJobs?.some((candidate) => candidate.id === job.id) === true
                            }
                          />
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p>暂无近期任务</p>
                  )}
                  <h4 className="font-medium">可重试任务</h4>
                  {status.data.retryableJobs === null ? (
                    <p>服务端未提供可重试任务范围</p>
                  ) : status.data.retryableJobs.length ? (
                    <ul className="space-y-3">
                      {status.data.retryableJobs.map((job) => (
                        <li key={job.id} className="space-y-2 rounded-lg border border-border p-3">
                          <ManagementJobRecord workflowId={context.workflowId} job={job} retryable />
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p>暂无可重试任务</p>
                  )}
                </div>
              </details>
              {status.data.round && (
                <details>
                  <summary className="cursor-pointer text-primary">扫描轮次详情</summary>
                  <Fields
                    fields={[
                      ['轮次 ID', status.data.round.id],
                      ['条目总数', status.data.round.total],
                      ['批次数', status.data.round.batches],
                      ['完成批次', status.data.round.completedBatches],
                      ['范围开始', managementTime(status.data.round.fromMs)],
                      ['范围结束', managementTime(status.data.round.untilMs)],
                      ['证据截止', managementTime(status.data.round.evidenceCutoffMs)],
                      [
                        '延后原因',
                        Object.entries(status.data.round.deferred)
                          .map(([reason, count]) => `${managementReasons[reason] ?? reason}：${count}`)
                          .join('；') || '无',
                      ],
                    ]}
                  />
                </details>
              )}
            </>
          )}
        </section>
      )}
    </div>
  );
}
