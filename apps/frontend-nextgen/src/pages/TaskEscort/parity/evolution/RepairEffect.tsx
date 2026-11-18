import type { RepairDeployment, RepairRevision, RepairTask } from '@/domain/taskEscortParity/repairTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortEvolutionRead } from '@/hooks/taskEscortParity/useTaskEscortEvolution';
import { repairService } from '@/services/taskEscortParity/repairService';
import { useCallback, useState } from 'react';
import { JsonDetails, NoData, ReadSelect, ReadState } from './ReadPrimitives';
function EffectEvidence({
  context,
  taskId,
  deployment,
  revision,
}: {
  context: TaskEscortParityContext;
  taskId: string;
  deployment: RepairDeployment;
  revision: RepairRevision;
}) {
  const itemIds = JSON.stringify(revision.input.items.map((i) => i.itemId));
  const read = useCallback(
    (signal: AbortSignal) =>
      repairService.verification(taskId, deployment.id, context.userId!, signal, {
        workflowId: context.workflowId,
        revision: revision.revision,
        deployNumber: deployment.receipt!.deployNumber,
        candidateCommit: deployment.receipt!.candidateCommit,
        itemIds: JSON.parse(itemIds),
      }),
    [
      taskId,
      deployment.id,
      deployment.receipt?.deployNumber,
      deployment.receipt?.candidateCommit,
      context.workflowId,
      context.userId,
      revision.revision,
      itemIds,
    ],
  );
  const result = useTaskEscortEvolutionRead(context, `verification:${taskId}:${deployment.id}`, read);
  if (
    result.data &&
    (result.data.evidence.candidateCommit !== deployment.receipt?.candidateCommit ||
      result.data.evidence.deployNumber !== deployment.receipt?.deployNumber)
  )
    return <ReadState error="验证证据与发布回执不匹配，已阻止展示" />;
  return (
    <div className="space-y-3">
      <ReadState {...result} />
      {result.data && (
        <>
          <p className="text-sm">
            观察部署 #{deployment.receipt!.deployNumber} · 实际样本 {result.data.evidence.total} ·{' '}
            {result.data.evidence.status}
          </p>
          <p className="text-xs text-muted-foreground">{result.data.evidence.note}</p>
          {result.data.evidence.total === 0 && (
            <p role="status" className="text-sm text-muted-foreground">
              暂无观察样本，尚不能判断有效或成功。
            </p>
          )}
          <JsonDetails label="发布后实际指标" value={result.data.evidence.metrics} open />
          <JsonDetails label="发布前基线" value={result.data.evidence.baseline} />
          <JsonDetails label="实际运行样本" value={result.data.evidence.samples} />
          <JsonDetails
            label="复发与自动观察"
            value={{
              recurrence: result.data.evidence.recurrence,
              automatic: result.data.evidence.automatic,
              excludedUnknownVersions: result.data.evidence.excludedUnknownVersions,
            }}
          />
          <JsonDetails label="实际验证结论（不由本页生成）" value={result.data.conclusions} />
        </>
      )}
    </div>
  );
}

export function RepairEffect({
  context,
  detail,
  revision,
}: {
  context: TaskEscortParityContext;
  detail: RepairTask;
  revision: RepairRevision;
}) {
  const releases =
    detail.release?.deployments.filter(
      (d) =>
        d.revision === revision.revision &&
        d.status === 'succeeded' &&
        d.receipt &&
        d.receipt.candidateCommit === revision.draft?.candidateCommit,
    ) ?? [];
  const [selected, setSelected] = useState('');
  const deployment = releases.find((d) => d.id === selected) ?? releases[0];
  return (
    <section aria-label="发布后效果" className="space-y-4">
      <h3 className="font-semibold">效果观察</h3>
      {!deployment ? (
        <NoData text="暂无成功发布回执，不能确认发布后效果" />
      ) : (
        <>
          <ReadSelect
            label="观察发布记录"
            value={deployment.id}
            onChange={setSelected}
            options={releases.map((d) => ({ value: d.id, label: `部署 #${d.receipt!.deployNumber} · ${d.id}` }))}
          />
          <EffectEvidence
            key={deployment.id}
            context={context}
            taskId={detail.taskId}
            deployment={deployment}
            revision={revision}
          />
        </>
      )}
    </section>
  );
}
