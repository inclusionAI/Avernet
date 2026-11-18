import type {
  ManagementAutomationStatus,
  ManagementJob,
  ManagementPolicy,
  ManagementRecordPage,
} from '@/domain/taskEscortParity/managementTypes';
import {
  ManagementProtocolError,
  array,
  bool,
  count,
  enumeration,
  identity,
  milliseconds,
  nullableCount,
  nullableText,
  object,
  strings,
  text,
} from './managementValidation';
export function policy(value: unknown, workflowId: string): ManagementPolicy {
  const d = object(value);
  identity(d.workflowId, workflowId);
  const dailyTime = text(d.dailyTime);
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(dailyTime)) throw new ManagementProtocolError('每日计划时间协议不完整');
  return {
    workflowId,
    enabled: bool(d.enabled),
    autoDraft: bool(d.autoDraft),
    dailyTime,
    timezone: enumeration(d.timezone, ['Asia/Shanghai']),
    executionEnvironment:
      d.executionEnvironment === null || d.executionEnvironment === undefined
        ? null
        : enumeration(d.executionEnvironment, ['pre', 'prod'] as const),
    minimumImpact:
      d.minimumImpact === null || d.minimumImpact === undefined
        ? null
        : enumeration(d.minimumImpact, ['medium', 'high'] as const),
    notificationRecipients:
      d.notificationRecipients === null || d.notificationRecipients === undefined
        ? null
        : strings(d.notificationRecipients),
    notificationVersion: nullableCount(d.notificationVersion),
    version: count(d.version),
    updatedAtMs: milliseconds(d.updatedAtMs),
    dailyConfigured: bool(d.dailyConfigured),
    source: enumeration(d.source, ['database', 'environment', 'default']),
  };
}
export function job(value: unknown): ManagementJob {
  const d = object(value);
  return {
    id: text(d.id),
    kind: text(d.kind),
    status: text(d.status),
    attempts: count(d.attempts),
    reason: nullableText(d.reason),
    taskId: nullableText(d.taskId),
    createdAtMs: d.createdAtMs === null || d.createdAtMs === undefined ? null : milliseconds(d.createdAtMs),
    flowId: nullableText(d.flowId),
    analysisId: nullableText(d.analysisId),
    complete: d.complete === null || d.complete === undefined ? null : bool(d.complete),
  };
}
export function automationStatus(value: unknown, workflowId: string): ManagementAutomationStatus {
  const d = object(value);
  identity(d.workflowId, workflowId);
  enumeration(d.schemaVersion, ['workflow-automation/v1']);
  const c = object(d.coverage);
  let round: ManagementAutomationStatus['round'] = null;
  if (d.round !== null && d.round !== undefined) {
    const r = object(d.round);
    round = {
      id: text(r.id),
      total: count(r.total),
      batches: count(r.batches),
      completedBatches: count(r.completedBatches),
      deferred: Object.fromEntries(Object.entries(object(r.deferred)).map(([k, v]) => [k, count(v)])),
      fromMs: milliseconds(r.fromMs),
      untilMs: milliseconds(r.untilMs),
      evidenceCutoffMs: milliseconds(r.evidenceCutoffMs),
    };
  }
  return {
    workflowId,
    lastScannedAtMs:
      d.lastScannedAtMs === null || d.lastScannedAtMs === undefined ? null : milliseconds(d.lastScannedAtMs),
    pauseReason: nullableText(d.pauseReason),
    coverage: {
      total: count(c.total),
      pending: count(c.pending),
      failed: count(c.failed),
      partial: count(c.partial),
      unknown: count(c.unknown),
    },
    jobs: array(d.jobs).map(job),
    retryableJobs: d.retryableJobs === null || d.retryableJobs === undefined ? null : array(d.retryableJobs).map(job),
    round,
  };
}
export function recordPage(value: unknown): ManagementRecordPage {
  const d = object(value);
  if (d.nextCursor !== null && (typeof d.nextCursor !== 'string' || !d.nextCursor))
    throw new ManagementProtocolError('执行记录游标协议不完整');
  return { jobs: array(d.jobs).map(job), nextCursor: d.nextCursor as string | null };
}
