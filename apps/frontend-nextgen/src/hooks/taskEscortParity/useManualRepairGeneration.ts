import { issueSnapshotKey } from '@/domain/taskEscortParity/evolution';
import type { RepairCandidates, RepairItem } from '@/domain/taskEscortParity/repairTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { repairService } from '@/services/taskEscortParity/repairService';
import { extractFriendlyErrorMessage, getErrorStatus } from '@/utils/requestErrorHandler';
import { useLayoutEffect, useRef, useState } from 'react';

export type GenerationChoice = {
  checked: boolean;
  disabled: boolean;
  count: number;
  reason?: string;
  toggle: () => void;
};

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;
}

function generationItemIds(item: RepairItem, workflowId: string): string[] {
  const eligible = (source: unknown): source is Record<string, unknown> & { itemId: string } => {
    const value = asRecord(source);
    return (
      !!value &&
      typeof value.itemId === 'string' &&
      !!value.itemId.trim() &&
      value.sourceAvailable === true &&
      value.state === 'pending' &&
      !value.activeTaskId &&
      (value.workflowId === undefined || value.workflowId === workflowId)
    );
  };
  if (!eligible(item)) return [];
  if (!('aggregation' in item) || item.aggregation == null) return [item.itemId];
  // Exact display folding is not an executable item. Use all confirmed original identities, never just its first row.
  const plan = asRecord(item.aggregation);
  if (!Array.isArray(plan?.originals) || !Array.isArray(plan.itemIds) || !plan.originals.every(eligible)) return [];
  const ids = plan.originals.map((source) => source.itemId);
  const members = new Set(ids);
  return ids.length === members.size &&
    members.has(item.itemId) &&
    plan.itemIds.length === ids.length &&
    new Set(plan.itemIds).size === ids.length &&
    plan.itemIds.every((id) => members.has(id)) &&
    (ids.length === 1 || (plan.status === 'ready' && plan.relation === 'exact'))
    ? ids
    : [];
}

export function useManualRepairGeneration(
  context: TaskEscortParityContext,
  data: RepairCandidates,
  visibleItems: RepairItem[],
  manualGeneration: boolean,
) {
  const [selected, setSelected] = useState<string[]>([]);
  const [instructions, setInstructions] = useState('');
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const request = useRef<{ payloadKey: string; id: string }>();
  const inFlight = useRef<AbortController>();
  useLayoutEffect(() => () => inFlight.current?.abort(), []);

  const memberIds = manualGeneration ? visibleItems.map((item) => generationItemIds(item, data.workflowId)) : [];
  const selectedIds = [
    ...new Set(memberIds.flatMap((ids, i) => (selected.includes(visibleItems[i].itemId) ? ids : []))),
  ].sort();
  const snapshot = data.activeSnapshot;
  const activeDeployment =
    snapshot?.status === 'resolved' &&
    snapshot.snapshot?.kind === 'deployment' &&
    snapshot.snapshot.workflowId === data.workflowId;
  const blockedReason = !manualGeneration
    ? null
    : !data.capabilities.generation
    ? data.capabilities.reason || '生成服务暂不可用。'
    : snapshot && !activeDeployment
    ? '当前生效版本未确认，暂不能生成草稿。'
    : selectedIds.length > data.limits.maxItems
    ? `最多可选择 ${data.limits.maxItems} 条原始建议。`
    : !selectedIds.length
    ? '请选择当前页中待处理且来源可用的修复建议。'
    : null;

  const submit = async () => {
    if (!manualGeneration || blockedReason || inFlight.current || created || needsRefresh || !context.userId) return;
    setError(null);
    const input = {
      workflowId: data.workflowId,
      itemIds: selectedIds,
      inputDigest: data.inputDigest,
      instructions: instructions.trim(),
      includeHistorical: data.includeHistorical,
      ...(activeDeployment ? { targetSnapshotKey: issueSnapshotKey(snapshot) } : {}),
    };
    const payloadKey = JSON.stringify(input);
    // A retry of the same submission must not create a second draft after an unknown outcome.
    if (request.current?.payloadKey !== payloadKey) {
      const id =
        globalThis.crypto.randomUUID?.() ??
        Array.from(globalThis.crypto.getRandomValues(new Uint8Array(16)), (byte) =>
          byte.toString(16).padStart(2, '0'),
        ).join('');
      request.current = { payloadKey, id };
    }
    const payload = { ...input, requestId: request.current.id };
    if (new Blob([JSON.stringify(payload)]).size > data.limits.maxRequestBytes) {
      setError('请求内容过大，请缩短要求或减少选择。');
      return;
    }
    const controller = new AbortController();
    inFlight.current = controller;
    setBusy(true);
    try {
      const taskId = await repairService.createManual(payload, context.userId, controller.signal);
      if (controller.signal.aborted) return;
      setCreated(true);
      context.navigate('issues', {
        repairTaskId: taskId,
        issueSignature: undefined,
        analysisId: undefined,
        runId: undefined,
      });
    } catch (cause) {
      if (controller.signal.aborted) return;
      const status = getErrorStatus(cause);
      setNeedsRefresh(status === 409 || status === 404);
      setError(extractFriendlyErrorMessage(cause, '无法提交优化任务，请重试。'));
    } finally {
      inFlight.current = undefined;
      if (!controller.signal.aborted) setBusy(false);
    }
  };

  const selectionFor = (id: string, index: number): GenerationChoice | undefined => {
    if (!manualGeneration) return undefined;
    const count = memberIds[index].length;
    return {
      checked: selected.includes(id),
      disabled: busy || created || needsRefresh || !count,
      count,
      reason: count ? undefined : '仅待处理、来源可用的原始建议或等价建议可用于生成。',
      toggle: () => {
        setSelected(ids => ids.includes(id) ? ids.filter(itemId => itemId !== id) : [...ids, id]);
        setError(null);
      },
    };
  };
  return { selectionFor, selectedCount: selectedIds.length, instructions, setInstructions, blockedReason,
    busy, created, error, needsRefresh, isSubmitting: () => !!inFlight.current, submit };
}
