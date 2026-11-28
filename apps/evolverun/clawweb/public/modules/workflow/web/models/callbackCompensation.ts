import type {
  CallbackCompensationBatch,
  CallbackCompensationCapabilities,
  CallbackCompensationPreview,
  CallbackCompensationRange,
} from './callbackCompensationTypes';

export function callbackCompensationStorageKey(scope: {
  environment: string;
  scopeKey: string;
  userId?: string;
  workflowId: string;
}) {
  return `callback-compensation:batch:${JSON.stringify([
    scope.environment,
    scope.scopeKey,
    scope.userId ?? '',
    scope.workflowId,
  ])}`;
}
export function callbackCompensationLocalDate(date: Date) {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}`;
}
export function callbackCompensationDefaultRange(now = Date.now()) {
  const to = Math.floor(now / 60000) * 60000;
  return {
    from: callbackCompensationLocalDate(new Date(to - 86400000)),
    to: callbackCompensationLocalDate(new Date(to)),
  };
}
export function callbackCompensationRange(
  configId: string,
  from: string,
  to: string,
  runIds: string,
  mode: 'time' | 'ids' = runIds.trim() ? 'ids' : 'time',
): CallbackCompensationRange {
  if (!configId.trim()) throw new Error('请选择服务端提供的接收配置');
  if (mode === 'ids') {
    const flowIds = Array.from(
      new Set(
        runIds
          .split(/[\s,，]+/u)
          .map((id) => id.trim())
          .filter(Boolean),
      ),
    );
    if (!flowIds.length) throw new Error('请至少输入一个运行 ID');
    if (flowIds.length > 100) throw new Error('最多支持 100 个运行 ID');
    return { configId, flowIds };
  }
  const start = new Date(from),
    end = new Date(to);
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || start >= end)
    throw new Error('请选择有效的绝对时间范围，开始时间必须早于结束时间');
  if (end.getTime() - start.getTime() > 31 * 86400000) throw new Error('单次时间范围最多 31 天');
  return { configId, from: start.toISOString(), to: end.toISOString() };
}
export function callbackCompensationReasonValid(reason: string) {
  const length = reason.trim().length;
  return length >= 10 && length <= 500;
}
export function callbackCompensationExecutionReady(capabilities: CallbackCompensationCapabilities | null) {
  return (
    capabilities?.canPreview === true &&
    capabilities.executionEnabled === true &&
    capabilities.blockedReasons.length === 0
  );
}
export function callbackCompensationCanExecute(input: {
  capabilities: CallbackCompensationCapabilities | null;
  preview: CallbackCompensationPreview | null;
  itemIds: string[];
  reason: string;
  acknowledged: boolean;
  now: number;
}) {
  const { capabilities, preview, itemIds, reason, acknowledged, now } = input;
  if (
    !callbackCompensationExecutionReady(capabilities) ||
    !capabilities ||
    !preview ||
    !acknowledged ||
    !callbackCompensationReasonValid(reason) ||
    !itemIds.length ||
    new Set(itemIds).size !== itemIds.length ||
    preview.workflowId !== capabilities.workflowId ||
    preview.environment !== capabilities.environment ||
    !capabilities.configs.some((config) => config.configId === preview.receiver.configId) ||
    !Number.isFinite(Date.parse(preview.expiresAt)) ||
    Date.parse(preview.expiresAt) <= now
  )
    return false;
  return itemIds.every((id) => preview.items.some((item) => item.itemId === id && item.status === 'eligible'));
}

export function callbackCompensationBatchResolved(batch: CallbackCompensationBatch | null) {
  return (
    !!batch &&
    ['stopped', 'completed'].includes(batch.status) &&
    batch.items.every((item) => !item.auditPending && item.state !== 'queued' && item.state !== 'sending')
  );
}
