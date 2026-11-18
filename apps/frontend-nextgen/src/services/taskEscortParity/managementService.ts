import type {
  ManagementAccess,
  ManagementHistory,
  ManagementRecordQuery,
} from '@/domain/taskEscortParity/managementTypes';
import { managementController as api } from '@/services/backendApi/harnessFlow/managementController';
import { automationStatus, policy, recordPage } from './managementAutomationMapper';
import { callback, facade, notification, permission } from './managementConfigMapper';
import { ManagementReadError, managementNotificationKeys } from './managementError';
import { historyItem, snapshot } from './managementHistoryMapper';
import { ManagementProtocolError, array, bool, count, identity, object } from './managementValidation';

async function read<T>(workflowId: string, userId: string, load: () => Promise<T>): Promise<T> {
  if (!workflowId.trim() || !userId.trim()) throw new Error('缺少明确的工作流或用户身份');
  try {
    return await load();
  } catch (error) {
    const keys = managementNotificationKeys(error);
    if (error instanceof Error && error.name === 'AbortError') throw new ManagementReadError('请求已取消', keys, true);
    const e = error as { status?: number; response?: { status?: number } } | null;
    const upstreamStatus = e?.status ?? e?.response?.status;
    const status =
      typeof upstreamStatus === 'number' &&
      Number.isInteger(upstreamStatus) &&
      upstreamStatus >= 100 &&
      upstreamStatus <= 599
        ? upstreamStatus
        : undefined;
    const labels: Record<number, string> = {
      401: '身份已失效，请重新登录',
      403: '无权读取此配置',
      404: '记录不存在或已删除',
      409: '来源版本已变化，请刷新',
      503: '服务暂不可用',
    };
    // Never surface raw upstream bodies: configuration errors may echo credentials.
    if (status) throw new ManagementReadError(`${status}：${labels[status] ?? '读取失败，请重试'}`, keys);
    if (error instanceof ManagementProtocolError) throw new ManagementReadError(error.message, keys);
    throw new ManagementReadError('读取失败，请重试', keys);
  }
}
export const managementService = {
  access: (id: string, user: string, signal: AbortSignal) =>
    read(id, user, async (): Promise<ManagementAccess> => {
      const d = object(await api.access(id, user, signal));
      if (d.workflowId !== undefined) identity(d.workflowId, id);
      return { canView: bool(d.canView), canEdit: bool(d.canEdit) };
    }),
  history: (id: string, user: string, signal: AbortSignal) =>
    read(id, user, async (): Promise<ManagementHistory> => {
      const d = object(await api.history(id, user, signal));
      if (d.workflowId !== undefined) identity(d.workflowId, id);
      if (d.active === undefined) throw new ManagementProtocolError('当前版本协议不完整');
      return { history: array(d.history).map(historyItem), active: d.active === null ? null : historyItem(d.active) };
    }),
  version: (id: string, user: string, version: number, signal: AbortSignal) =>
    read(id, user, async () => snapshot(await api.version(id, user, count(version), signal), id, { version })),
  deploy: (id: string, user: string, deployNumber: number, signal: AbortSignal) =>
    read(id, user, async () => snapshot(await api.deploy(id, user, count(deployNumber), signal), id, { deployNumber })),
  permissions: (id: string, user: string, signal: AbortSignal) =>
    read(id, user, async () => array(await api.permissions(id, user, signal)).map(permission)),
  facades: (id: string, user: string, signal: AbortSignal) =>
    read(id, user, async () =>
      array(await api.facades(user, signal))
        .map(facade)
        .filter((f) => f.workflowId === id),
    ),
  notification: (id: string, user: string, signal: AbortSignal) =>
    read(id, user, async () => notification(await api.notification(id, user, signal), id)),
  callbacks: (id: string, user: string, signal: AbortSignal) =>
    read(id, user, async () => array(await api.callbacks(id, user, signal)).map((v) => callback(v, id))),
  policy: (id: string, user: string, signal: AbortSignal) =>
    read(id, user, async () => policy(await api.policy(id, user, signal), id)),
  status: (id: string, user: string, signal: AbortSignal) =>
    read(id, user, async () => automationStatus(await api.status(id, user, signal), id)),
  records: (id: string, user: string, query: ManagementRecordQuery, signal: AbortSignal) =>
    read(id, user, async () => recordPage(await api.records(id, user, query, signal))),
};
