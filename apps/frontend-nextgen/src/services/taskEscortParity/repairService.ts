import type { RepairQuery, RepairVerification, RepairVerificationScope } from '@/domain/taskEscortParity/repairTypes';
import { repairController as api, type CreateManualRepairRequest } from '@/services/backendApi/harnessFlow/repairController';
import { array, count, match, object, text } from './evolutionMapper';
import { evolutionRead } from './evolutionService';
import { candidatesView, repairItemView, taskView } from './repairMapper';
export const repairService = {
  createManual: (input: CreateManualRepairRequest, user: string, signal: AbortSignal) => api.createManual(input, user, signal),
  candidates: (wf: string, user: string, query: RepairQuery, signal: AbortSignal) =>
    evolutionRead(async () => {
      const first = await api.candidates(wf, user, { ...query, metadataPage: 1 }, signal);
      const result = candidatesView(first, wf);
      const pages = first.metadata?.totalPages ?? 1;
      for (let page = 2; page <= pages; page++) {
        signal.throwIfAborted();
        const next = await api.candidates(wf, user, { ...query, metadataPage: page }, signal);
        if (
          next.canEdit !== first.canEdit ||
          next.canOptimize !== first.canOptimize ||
          next.inputDigest !== first.inputDigest ||
          next.metadata?.digest !== first.metadata?.digest ||
          next.metadata?.totalPages !== pages
        )
          throw new Error('409：建议来源已变化，请刷新');
        const mapped = candidatesView(next, wf);
        result.tasks.push(...mapped.tasks);
        result.facets.nodes.push(...mapped.facets.nodes);
        result.facets.failureModes.push(...mapped.facets.failureModes);
      }
      return result;
    }),
  item: (wf: string, id: string, user: string, signal: AbortSignal) =>
    evolutionRead(async () => {
      const d = repairItemView(await api.item(wf, id, user, signal), wf);
      match(d.itemId, id);
      return d;
    }),
  task: (wf: string, id: string, user: string, page: number, signal: AbortSignal) =>
    evolutionRead(async () => taskView(await api.task(id, user, page, signal), wf, id)),
  material: (taskId: string, revision: number, itemId: string, user: string, signal: AbortSignal) =>
    evolutionRead(async () => {
      const d = repairItemView(await api.material(taskId, revision, itemId, user, signal));
      match(d.itemId, itemId);
      return d;
    }),
  diff: (taskId: string, revision: number, base: 'baseline' | 'parent', user: string, signal: AbortSignal) =>
    evolutionRead(async () => {
      const d = await api.diff(taskId, revision, base, user, signal);
      object(d);
      if (typeof d.truncated !== 'boolean') throw new Error('差异协议不完整');
      return {
        baseCommit: text(d.baseCommit),
        candidateCommit: text(d.candidateCommit),
        truncated: d.truncated,
        files: array(d.files).map((f) => {
          object(f);
          if (typeof f.binary !== 'boolean' || typeof f.truncated !== 'boolean') throw new Error('文件差异协议不完整');
          return { ...f, path: text(f.path), status: text(f.status) };
        }),
      };
    }),
  trials: (taskId: string, offset: number, user: string, signal: AbortSignal) =>
    evolutionRead(async () => {
      const d = await api.trials(taskId, offset, user, signal);
      if (typeof d.configured !== 'boolean') throw new Error('试运行协议不完整');
      return {
        configured: d.configured,
        trials: array(d.trials).map(object),
        nextOffset: d.nextOffset === null || d.nextOffset === undefined ? null : count(d.nextOffset),
      };
    }),
  verification: (
    taskId: string,
    deploymentId: string,
    user: string,
    signal: AbortSignal,
    expected: RepairVerificationScope,
  ) =>
    evolutionRead(async (): Promise<RepairVerification> => {
      const d = await api.verification(taskId, deploymentId, user, signal);
      const e = object(d.evidence);
      match(e.deploymentId, deploymentId);
      match(e.deployNumber, expected.deployNumber);
      match(e.candidateCommit, expected.candidateCommit);
      const samples = array(e.samples as Record<string, unknown>[]).map((v) => {
        const sample = object(v);
        match(sample.deployNumber, expected.deployNumber);
        return sample;
      });
      // Upstream intentionally returns task-wide conclusions, not only this deployment.
      const conclusions = array(d.conclusions)
        .map(object)
        .filter((c) => c.deploymentId === deploymentId)
        .map((c) => {
          match(c.workflowId, expected.workflowId);
          match(c.taskId, taskId);
          match(c.revision, expected.revision);
          match(c.deployNumber, expected.deployNumber);
          if (!expected.itemIds.includes(text(c.itemId))) throw new Error('验证结论目标不属于所选冻结版本');
          return c;
        });
      return {
        evidence: {
          ...e,
          deploymentId,
          deployNumber: count(e.deployNumber),
          candidateCommit: text(e.candidateCommit),
          samples,
          total: count(e.total),
          status: text(e.status),
          note: text(e.note),
          metrics: object(e.metrics),
          recurrence: array(e.recurrence as Record<string, unknown>[]).map(object),
        },
        conclusions,
      };
    }),
};
