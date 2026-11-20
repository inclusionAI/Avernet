import { Button } from '@/components/ui/Button';
import { displayDate, statusLabels } from '@/domain/taskEscortParity/evolution';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortRepairTask } from '@/hooks/taskEscortParity/useTaskEscortRepair';
import { useState } from 'react';
import { JsonDetails, NoData, ReadCapabilities, ReadPager, ReadSelect, ReadState, ReadTabs } from './ReadPrimitives';
import { RepairChanges } from './RepairChanges';
import { RepairEffect } from './RepairEffect';
import { RepairTargets } from './RepairTargets';
interface RepairTaskWorkspaceProps {
  context: TaskEscortParityContext;
  taskId: string;
  canEdit: boolean;
}
export function RepairTaskWorkspace(props: RepairTaskWorkspaceProps) {
  const { context, taskId } = props;
  return (
    <RepairTaskDetail key={JSON.stringify([context.scopeKey, context.userId, context.workflowId, taskId])} {...props} />
  );
}
function RepairTaskDetail({ context, taskId, canEdit }: RepairTaskWorkspaceProps) {
  const [historyPage, setHistoryPage] = useState(1),
    [selected, setSelected] = useState<number | null>(null),
    [tab, setTab] = useState<'targets' | 'changes' | 'effect'>('targets');
  const valid = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(taskId);
  const result = useTaskEscortRepairTask(context, valid ? taskId : '', historyPage);
  if (!valid) return <ReadState error="修复任务 ID 无效" />;
  const detail = result.data;
  const revisions = detail
    ? [
        ...new Map(
          [
            ...detail.revisions,
            ...(detail.latestSuccessful ? [detail.latestSuccessful] : []),
            detail.latestAttempt,
          ].map((r) => [r.revision, r]),
        ).values(),
      ].sort((a, b) => b.revision - a.revision)
    : [];
  const revision = revisions.find((r) => r.revision === (selected ?? detail?.latestAttempt.revision));
  return (
    <section aria-label="Workflow 修复工作区" className="w-full space-y-5 text-foreground">
      <Button variant="outline" size="sm" onClick={() => context.navigate('issues', { repairTaskId: undefined })}>
        返回问题与优化
      </Button>
      <ReadState {...result} />
      {detail && (
        <>
          <header className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-2">
              <h2 className="text-xl font-semibold">{detail.title || '优化任务'}</h2>
              <p className="break-all font-mono text-xs text-muted-foreground">
                {detail.taskId} · 工作流 {detail.workflowId}
              </p>
              <p className="text-sm">
                最新尝试 v{detail.latestAttempt.revision} ·{' '}
                {statusLabels[detail.latestAttempt.phase] ?? detail.latestAttempt.phase} · 最近成功候选{' '}
                {detail.latestSuccessful ? `v${detail.latestSuccessful.revision}` : '无'}
              </p>
            </div>
            <ReadSelect
              label="修复版本"
              value={String(selected ?? detail.latestAttempt.revision)}
              onChange={(v) => setSelected(Number(v))}
              options={revisions.map((r) => ({
                value: String(r.revision),
                label: `v${r.revision} · ${statusLabels[r.phase] ?? r.phase}`,
              }))}
            />
          </header>
          <details className="rounded-lg border border-border p-3">
            <summary className="cursor-pointer text-sm font-medium">版本历史</summary>
            <div className="mt-3 space-y-2">
              {detail.revisions.map((r) => (
                <div
                  key={r.revision}
                  className="flex flex-wrap items-center justify-between gap-2 border-t border-border py-2 text-xs"
                >
                  <span>
                    v{r.revision} · {statusLabels[r.phase] ?? r.phase} · {displayDate(r.updatedAtMs)}
                  </span>
                  <Button variant="outline" size="sm" onClick={() => setSelected(r.revision)}>
                    选择版本 v{r.revision}
                  </Button>
                </div>
              ))}
            </div>
            {detail.revisionPage && (
              <ReadPager
                label="版本历史"
                page={historyPage}
                totalPages={detail.revisionPage.totalPages}
                total={detail.revisionPage.total}
                onChange={setHistoryPage}
              />
            )}
          </details>
          <ReadTabs
            label="优化任务分区"
            value={tab}
            onChange={setTab}
            options={[
              { value: 'targets', label: '优化目标' },
              { value: 'changes', label: '改动与发布' },
              { value: 'effect', label: '发布后效果' },
            ]}
          />
          {revision ? (
            <>
              <p className="text-xs text-muted-foreground">
                所选 v{revision.revision} · {statusLabels[revision.phase] ?? revision.phase}
              </p>
              {tab === 'targets' ? (
                <RepairTargets key={`${taskId}:${revision.revision}`} context={context} revision={revision} />
              ) : tab === 'changes' ? (
                <RepairChanges
                  key={`${taskId}:${revision.revision}`}
                  context={context}
                  detail={detail}
                  revision={revision}
                  canEdit={canEdit}
                  onRefresh={result.refresh}
                />
              ) : (
                <RepairEffect
                  key={`${taskId}:${revision.revision}`}
                  context={context}
                  detail={detail}
                  revision={revision}
                />
              )}
            </>
          ) : (
            <NoData text="所选版本不在当前历史页，请在版本历史中重新选择" />
          )}
          <ReadCapabilities capabilities={detail.capabilities} canEdit={canEdit} />
          <JsonDetails label="任务实际执行状态" value={detail.executions ?? detail.execution ?? null} />
          {detail.automaticProgress && <JsonDetails label="自动批次进度" value={detail.automaticProgress} />}
          <p className="rounded-lg border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
            此详情页不提供重新生成、应用建议或启动试运行。
          </p>
        </>
      )}
    </section>
  );
}
