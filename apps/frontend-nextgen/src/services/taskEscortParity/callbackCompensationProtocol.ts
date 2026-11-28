import type {
  CallbackCompensationBatch,
  CallbackCompensationCapabilities,
  CallbackCompensationEligibility,
  CallbackCompensationItemState,
  CallbackCompensationPreview,
  CallbackCompensationReceiver,
  CallbackCompensationScope,
} from '@/domain/taskEscortParity/callbackCompensationTypes';

function invalid(): never {
  throw new Error('补偿服务响应与当前工作流契约不一致');
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid();
  return value as Record<string, unknown>;
}
function text(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value : invalid();
}
function boolean(value: unknown): boolean {
  return typeof value === 'boolean' ? value : invalid();
}
function number(value: unknown): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : invalid();
}
function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : invalid();
}
function strings(value: unknown): string[] {
  return array(value).map(text);
}
function member<T extends string>(value: unknown, values: readonly T[]): T {
  return typeof value === 'string' && values.includes(value as T) ? (value as T) : invalid();
}
function unique<T>(items: T[], key: (item: T) => string): T[] {
  return new Set(items.map(key)).size === items.length ? items : invalid();
}
function receiver(value: unknown): CallbackCompensationReceiver {
  const row = object(value);
  const origin = text(row.targetOrigin);
  let safeOrigin = '目标 Origin 不可用';
  try {
    const url = new URL(origin);
    if (['https:', 'http:'].includes(url.protocol) && !url.username && !url.password && url.origin === origin)
      safeOrigin = origin;
  } catch {
    /* Invalid server configs remain inspectable, not executable. */
  }
  // Deliberately allowlist; extra config fields (including secrets) never enter UI state.
  return {
    configId: text(row.configId),
    name: text(row.name),
    targetOrigin: safeOrigin,
    ...(row.receiverId === undefined ? {} : { receiverId: text(row.receiverId) }),
  };
}
function scope(row: Record<string, unknown>, expected: CallbackCompensationScope) {
  if (row.workflowId !== expected.workflowId || row.environment !== expected.environment) invalid();
}
export function parseCallbackCompensationCapabilities(
  value: unknown,
  workflowId: string,
): CallbackCompensationCapabilities {
  const row = object(value);
  if (row.workflowId !== workflowId || row.canPreview !== true) return invalid();
  return {
    workflowId,
    environment: text(row.environment),
    canPreview: true,
    executionEnabled: boolean(row.executionEnabled),
    blockedReasons: strings(row.blockedReasons),
    configs: unique(array(row.configs).map(receiver), (config) => config.configId),
  };
}
export function parseCallbackCompensationPreview(
  value: unknown,
  expected: CallbackCompensationScope,
  configId: string,
): CallbackCompensationPreview {
  const row = object(value);
  scope(row, expected);
  const target = receiver(row.receiver);
  if (target.configId !== configId) return invalid();
  const expiresAt = text(row.expiresAt);
  if (!Number.isFinite(Date.parse(expiresAt))) return invalid();
  const summary = object(row.summary);
  const items = unique(
    array(row.items).map((value) => {
      const item = object(value);
      if (!('payload' in item)) return invalid();
      return {
        itemId: text(item.itemId),
        flowId: text(item.flowId),
        status: member<CallbackCompensationEligibility>(item.status, ['eligible', 'delivered', 'review', 'ineligible']),
        reasons: strings(item.reasons),
        payload: item.payload,
      };
    }),
    (item) => item.itemId,
  );
  return {
    ...expected,
    previewId: text(row.previewId),
    receiver: target,
    expiresAt,
    items,
    summary: {
      eligible: number(summary.eligible),
      delivered: number(summary.delivered),
      review: number(summary.review),
      ineligible: number(summary.ineligible),
    },
  };
}
export function parseCallbackCompensationBatch(
  value: unknown,
  expected: CallbackCompensationScope,
  batchId?: string,
): CallbackCompensationBatch {
  const row = object(value);
  scope(row, expected);
  if (batchId !== undefined && row.batchId !== batchId) return invalid();
  const counts = object(row.counts);
  return {
    ...expected,
    batchId: text(row.batchId),
    status: member(row.status, ['queued', 'running', 'stopped', 'completed']),
    counts: Object.fromEntries(Object.entries(counts).map(([key, value]) => [key, number(value)])),
    items: unique(
      array(row.items).map((value) => {
        const item = object(value);
        return {
          itemId: text(item.itemId),
          flowId: text(item.flowId),
          state: member<CallbackCompensationItemState>(item.state, [
            'queued',
            'sending',
            'received',
            'failed',
            'unknown',
            'skipped',
          ]),
          auditPending: boolean(item.auditPending),
          ...(item.reason === undefined ? {} : { reason: text(item.reason) }),
          ...(item.httpStatus === undefined ? {} : { httpStatus: number(item.httpStatus) }),
        };
      }),
      (item) => item.itemId,
    ),
  };
}
