import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalTitle, ModalTrigger } from '@/components/ui/Modal';
import type { RepairRevision, RepairTask } from '@/domain/taskEscortParity/repairTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortEvolutionRead } from '@/hooks/taskEscortParity/useTaskEscortEvolution';
import {
  isPendingPublication,
  repairPublicationService as publication,
  repairPublishStatus,
  type RepairPublishIntent,
} from '@/services/taskEscortParity/repairPublicationService';
import { useCallback, useRef, useState } from 'react';

const hash = (value: unknown): value is string =>
  typeof value === 'string' && /^[a-f0-9]{40}(?:[a-f0-9]{24})?$/.test(value);

export function RepairPublication({
  context,
  detail,
  revision,
  canEdit,
  completeDiff,
  onRefresh,
}: {
  context: TaskEscortParityContext;
  detail: RepairTask;
  revision: RepairRevision;
  canEdit: boolean;
  completeDiff: boolean;
  onRefresh: () => void;
}) {
  const [review, setReview] = useState<{ stateVersion: number; pending: RepairPublishIntent | null } | null>(null);
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const inFlight = useRef(false);
  const { taskId, workflowId, latestSuccessful, latestAttempt } = detail;
  const { userId } = context;
  const { candidateCommit, packDigest } = revision.draft ?? {};
  const scope =
    context.ready && userId && workflowId === context.workflowId && hash(candidateCommit) && hash(packDigest)
      ? { userId, workflowId, taskId, revision: revision.revision, candidateCommit, packDigest }
      : null;
  const automated =
    detail.source === 'automatic' ||
    detail.revisions.some((r) => !!r.input.automation) ||
    !!latestAttempt.input.automation ||
    !!latestSuccessful?.input.automation;
  const automaticReady = !!revision.input.automation?.roundId && detail.automaticProgress?.publishable === true;
  // Manual optimization may be available without Editor edit access; the
  // task's publication capability and authorized workflow target govern it.
  const permitted = detail.capabilities.publication === true && (!automated || canEdit);
  const isCurrent =
    latestSuccessful?.revision === revision.revision &&
    latestSuccessful.stateVersion === revision.stateVersion &&
    latestSuccessful.phase === revision.phase &&
    latestSuccessful.draft?.candidateCommit === candidateCommit &&
    latestSuccessful.draft?.packDigest === packDigest &&
    ['review', 'failed', 'no_change', 'blocked'].includes(latestAttempt.phase);
  const deployments = detail.release?.deployments ?? [];
  const blocking = deployments.find((row) => row.status !== 'failed');
  const pending = deployments.find(
    (row) =>
      row.revision === revision.revision &&
      row.candidateCommit === candidateCommit &&
      row.packDigest === packDigest &&
      isPendingPublication(row.status),
  );
  const pendingTarget = pending?.target as { scope?: unknown; id?: unknown } | undefined;
  const canReconcile =
    !!scope &&
    permitted &&
    pendingTarget?.scope === 'workflow' &&
    typeof pendingTarget.id === 'string' &&
    !!pendingTarget.id;
  const canReview =
    !!scope &&
    permitted &&
    isCurrent &&
    (!revision.input.automation || automaticReady) &&
    (revision.phase === 'review' || (revision.phase === 'no_change' && automaticReady)) &&
    (revision.checks?.static as { status?: string } | undefined)?.status === 'passed' &&
    Number.isSafeInteger(revision.stateVersion) &&
    revision.stateVersion >= 0 &&
    !!detail.release &&
    !blocking &&
    completeDiff;
  const read = useCallback((signal: AbortSignal) => publication.target(taskId, userId!, signal), [taskId, userId]);
  const result = useTaskEscortEvolutionRead(
    context,
    `repair-publish-target:${taskId}:${revision.revision}`,
    read,
    !!review && canReview,
  );
  const target = result.data;
  const changed = !!review && (review.stateVersion !== revision.stateVersion || !canReview);
  const targetChanged = !!(
    review?.pending &&
    target &&
    (review.pending.targetId !== target.id || review.pending.environment !== target.environment)
  );
  const canSubmit = !!review && !changed && !targetChanged && !!target && ack;

  function openReview() {
    if (!canReview || !scope || inFlight.current) return;
    setError('');
    setAck(false);
    try {
      setReview({ stateVersion: revision.stateVersion, pending: publication.pending(scope) });
    } catch {
      setError('无法安全保存或读取原发布请求。请在 ClawWeb 核对状态后再操作。');
    }
  }

  async function publish() {
    if (!scope || !review || !canSubmit || !target || inFlight.current) return;
    const original = review.pending;
    let body: RepairPublishIntent;
    try {
      const saved = publication.pending(scope);
      if (saved?.requestId !== original?.requestId) throw new Error('Publish intent changed');
      body = saved ?? {
        requestId: crypto.randomUUID(),
        revision: revision.revision,
        expectedStateVersion: review.stateVersion,
        targetId: target.id,
        environment: target.environment,
        acknowledgeUncoveredChecks: true,
      };
      if (body.targetId !== target.id || body.environment !== target.environment)
        throw new Error('Publish target changed');
      if (!saved) publication.reserve(scope, body);
    } catch {
      setError('无法安全记录发布请求；本次没有提交。请返回核对后重试。');
      return;
    }
    inFlight.current = true;
    setBusy(true);
    setError('');
    try {
      await publication.publish(scope, body);
      publication.clear(scope, body.requestId);
      setReview(null);
      onRefresh();
    } catch (reason) {
      const status = repairPublishStatus(reason);
      if (!original && status && [400, 401, 403, 404, 413].includes(status)) {
        publication.clear(scope, body.requestId);
        setReview(null);
        setError(`服务器拒绝发布（HTTP ${status}）。请核对权限、发布范围及版本后重试。`);
      } else {
        setReview({ ...review, pending: body });
        setAck(false);
        setError(
          status === 409
            ? '服务器报告发布冲突。请返回并刷新任务，必要时在 ClawWeb 核对原请求；不会改用新请求。'
            : status === 401 || status === 403
            ? '当前账号无权重试发布，原请求仍保留。'
            : '未能确认发布结果。请核对发布记录，或重试同一请求；不会改用新请求。',
        );
      }
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  async function reconcile() {
    if (!canReconcile || !scope || !pending || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError('');
    try {
      await publication.reconcile(scope, pending);
      onRefresh();
    } catch {
      setError('无法核对发布状态，请重试核对原部署。');
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <section aria-label="候选发布" className="space-y-2 rounded-lg border border-border p-3">
      {blocking ? (
        <>
          <p className="text-sm text-muted-foreground">
            {blocking.status === 'succeeded'
              ? '已有发布记录，请查看实际回执。'
              : '已有发布记录，结果未确认前不可再次发布。'}
          </p>
          {canReconcile && (
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void reconcile()}>
              核对发布状态
            </Button>
          )}
        </>
      ) : (
        <>
          <Modal
            open={!!review}
            onOpenChange={(open) => {
              if (open) openReview();
              else if (!inFlight.current) {
                setReview(null);
                setError('');
              }
            }}
          >
            <ModalTrigger asChild>
              <Button size="sm" disabled={!canReview || busy}>
                发布此版本
              </Button>
            </ModalTrigger>
            {review && (
              <ModalContent showClose={!busy}>
                <ModalTitle>确认发布 v{revision.revision}</ModalTitle>
                <ModalDescription>
                  发布将应用此候选的工作流、脚本及资源改动；不会启动试运行，也不代表改动已验证有效。
                </ModalDescription>
                <p className="break-all font-mono text-xs text-muted-foreground">
                  工作流 {workflowId} · 候选 {scope?.candidateCommit}
                </p>
                {result.loading && <p role="status">正在核对工作流发布范围…</p>}
                {target && (
                  <p className="break-words text-sm">
                    {target.label} · {target.environment}
                  </p>
                )}
                {result.error && (
                  <div className="space-y-2">
                    <p role="alert" className="text-sm text-destructive">
                      {result.error}。尚未提交新的发布请求。
                    </p>
                    <Button size="sm" variant="outline" onClick={result.refresh}>
                      重新读取发布范围
                    </Button>
                  </div>
                )}
                {changed && <p role="alert">候选状态或审阅条件已变化。请返回重新审阅。</p>}
                {targetChanged && <p role="alert">原发布范围已变化或不再可用，不会向新范围重试。</p>}
                {review.pending && (
                  <p className="text-sm text-muted-foreground">只能按原版本、原目标和原请求编号重试。</p>
                )}
                {error && (
                  <p role="alert" className="text-sm text-destructive">
                    {error}
                  </p>
                )}
                <label className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-1 size-4 shrink-0"
                    checked={ack}
                    disabled={!target || changed || targetChanged || busy}
                    onChange={(event) => setAck(event.target.checked)}
                  />
                  我已审阅完整差异、检查报告和目标范围，接受未覆盖检查的风险，并同意不依赖试运行结果发布。
                </label>
                <ModalFooter>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => {
                      setReview(null);
                      setError('');
                    }}
                  >
                    返回审阅
                  </Button>
                  <Button size="sm" disabled={!canSubmit || busy} onClick={() => void publish()}>
                    {busy ? '正在提交…' : review.pending ? '重试原发布请求' : '确认发布'}
                  </Button>
                </ModalFooter>
              </ModalContent>
            )}
          </Modal>
          {!canReview && (
            <p className="text-xs text-muted-foreground">
              发布需要当前可审阅候选、完整的冻结基线差异及服务端发布权限。
            </p>
          )}
        </>
      )}
      {error && !review && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <Button size="sm" variant="outline" disabled={busy} onClick={onRefresh}>
        刷新任务状态
      </Button>
    </section>
  );
}
