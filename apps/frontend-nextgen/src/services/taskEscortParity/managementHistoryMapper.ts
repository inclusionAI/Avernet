import type {
  ManagementHistoryItem,
  ManagementSnapshot,
  ManagementVersionSource,
} from '@/domain/taskEscortParity/managementTypes';
import {
  ManagementProtocolError,
  bool,
  count,
  identity,
  nullableCount,
  nullableText,
  object,
  safeSpecJson,
  seconds,
  text,
} from './managementValidation';
export function versionSource(value: unknown, depth = 0): ManagementVersionSource | null {
  if (value === null || value === undefined) return null;
  if (depth > 8) throw new ManagementProtocolError('版本来源协议层级异常');
  const d = object(value);
  return {
    kind: text(d.kind),
    deployNumber: count(d.deployNumber),
    baselineDeployNumber: nullableCount(d.baselineDeployNumber),
    taskId: nullableText(d.taskId),
    revision: nullableCount(d.revision),
    automationRoundId: nullableText(d.automationRoundId),
    actorId: nullableText(d.actorId),
    effectiveAtMs: nullableCount(d.effectiveAtMs),
    restoredFromDeployNumber: nullableCount(d.restoredFromDeployNumber),
    ...(d.restoredSource === null || d.restoredSource === undefined
      ? {}
      : { restoredSource: versionSource(d.restoredSource, depth + 1) }),
  };
}
export function historyItem(value: unknown): ManagementHistoryItem {
  const d = object(value);
  return {
    version: count(d.version),
    deployNumber: count(d.deployNumber),
    tagName: nullableText(d.tagName),
    action: text(d.action),
    fromDeployNumber: nullableCount(d.fromDeployNumber),
    note: nullableText(d.note),
    botId: nullableText(d.botId),
    ownerId: nullableText(d.ownerId),
    isActive: bool(d.isActive),
    createdAtMs: seconds(d.gmtCreate),
    source: versionSource(d.source),
  };
}
export function snapshot(
  value: unknown,
  workflowId: string,
  locator: { version: number } | { deployNumber: number },
): ManagementSnapshot {
  const d = object(value);
  identity(d.workflowId, workflowId);
  for (const [key, expected] of Object.entries(locator))
    if (d[key] !== expected) throw new ManagementProtocolError('快照身份协议不匹配');
  return {
    workflowId,
    version: count(d.version),
    deployNumber: count(d.deployNumber),
    tagName: nullableText(d.tagName),
    action: text(d.action),
    note: nullableText(d.note),
    createdAtMs: seconds(d.gmtCreate),
    specJson: safeSpecJson(d.specJson),
  };
}
