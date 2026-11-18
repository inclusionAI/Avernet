import type { Snapshot } from '@/domain/taskEscortParity/evolutionTypes';
import type {
  RepairCandidates,
  RepairCapabilities,
  RepairDeployment,
  RepairItem,
  RepairRevision,
  RepairTask,
} from '@/domain/taskEscortParity/repairTypes';
import type {
  RepairCandidatesDto,
  RepairCapabilityDto,
  RepairItemDto,
  RepairRevisionDto,
  RepairTaskDto,
} from '@/services/backendApi/harnessFlow/repairController';
import { array, count, match, object, text } from './evolutionMapper';
export function capabilityView(d: RepairCapabilityDto): RepairCapabilities {
  object(d);
  if (['generation', 'diff', 'publication'].some((k) => typeof d[k as keyof RepairCapabilityDto] !== 'boolean'))
    throw new Error('修复能力协议不完整');
  return {
    generation: d.generation,
    diff: d.diff,
    publication: d.publication,
    reason: typeof d.reason === 'string' ? d.reason : null,
  };
}
export function repairItemView(d: RepairItemDto, workflowId?: string): RepairItem {
  object(d);
  if (workflowId && d.workflowId !== undefined) match(d.workflowId, workflowId);
  return {
    ...d,
    itemId: text(d.itemId),
    groupKey: text(d.groupKey),
    proposalKey: text(d.proposalKey),
    contentRevision: count(d.contentRevision),
    instruction: text(d.instruction),
    sources: array(d.sources).map(object),
    proposal: d.proposal === null ? null : object(d.proposal),
    context: d.context === undefined ? undefined : object(d.context),
  };
}
export function candidatesView(d: RepairCandidatesDto, workflowId: string): RepairCandidates {
  object(d);
  if (d.schemaVersion !== 'workflow-repair/v2') throw new Error('修复协议版本不支持');
  match(d.workflowId, workflowId);
  if (typeof d.canEdit !== 'boolean' || typeof d.includeHistorical !== 'boolean')
    throw new Error('修复权限/范围协议不完整');
  const p = object(d.page),
    f = object(d.facets),
    limits = object(d.limits);
  return {
    workflowId,
    inputDigest: text(d.inputDigest),
    items: array(d.items).map((i) => repairItemView(i, workflowId)),
    tasks: array(d.tasks).map((t) => ({
      ...t,
      taskId: text(t.taskId),
      revision: count(t.revision),
      phase: text(t.phase),
      updatedAtMs: count(t.updatedAtMs),
      itemCount: count(t.itemCount),
    })),
    capabilities: capabilityView(d.capabilities),
    activeSnapshot: (d.activeSnapshot as Snapshot) ?? null,
    includeHistorical: d.includeHistorical,
    facets: { nodes: array(f.nodes as string[]).map(text), failureModes: array(f.failureModes as string[]).map(text) },
    counts: Object.fromEntries(Object.entries(object(d.counts)).map(([k, v]) => [k, count(v)])),
    page: { page: count(p.page), pageSize: count(p.pageSize), total: count(p.total), totalPages: count(p.totalPages) },
    limits: { maxItems: count(limits.maxItems), maxRequestBytes: count(limits.maxRequestBytes) },
    canEdit: d.canEdit,
  };
}
function revisionView(d: RepairRevisionDto, wf: string, taskId: string): RepairRevision {
  object(d);
  match(d.workflowId, wf);
  match(d.taskId, taskId);
  const input = object(d.input);
  return {
    ...d,
    revision: count(d.revision),
    phase: text(d.phase),
    input: {
      ...input,
      items: array(d.input.items).map((i) => repairItemView(i, wf)),
      baseline: object(input.baseline),
    },
    draft: d.draft === null ? null : object(d.draft),
    checks: d.checks === null ? null : object(d.checks),
    error: d.error === null ? null : object(d.error),
  };
}
export function taskView(d: RepairTaskDto, wf: string, taskId: string): RepairTask {
  object(d);
  match(d.workflowId, wf);
  match(d.taskId, taskId);
  const deployments = d.release
    ? array(d.release.deployments).map((v) => {
        object(v);
        match(v.workflowId, wf);
        match(v.taskId, taskId);
        return {
          ...v,
          id: text(v.id),
          revision: count(v.revision),
          status: text(v.status),
          receipt: v.receipt === null ? null : object(v.receipt),
        } as RepairDeployment;
      })
    : [];
  return {
    ...d,
    workflowId: wf,
    taskId,
    latestAttempt: revisionView(d.latestAttempt, wf, taskId),
    latestSuccessful: d.latestSuccessful === null ? null : revisionView(d.latestSuccessful, wf, taskId),
    revisions: array(d.revisions).map((r) => revisionView(r, wf, taskId)),
    capabilities: capabilityView(d.capabilities),
    release: d.release ? { approval: d.release.approval, deployments } : undefined,
  };
}
