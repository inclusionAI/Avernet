import type {
  ManagementCallback,
  ManagementEventOptions,
  ManagementFacade,
  ManagementNotification,
  ManagementPermission,
} from '@/domain/taskEscortParity/managementTypes';
import {
  ManagementProtocolError,
  array,
  bool,
  count,
  identity,
  maskedUrl,
  nullableText,
  object,
  seconds,
  strings,
  text,
} from './managementValidation';
function permissionFlag(value: unknown): boolean {
  if (value !== 0 && value !== 1) throw new ManagementProtocolError('权限标记协议不完整');
  return value === 1;
}
export function permission(value: unknown): ManagementPermission {
  const d = object(value);
  return {
    id: count(d.id),
    botId: nullableText(d.botId),
    botOwnerId: text(d.botOwnerId),
    canView: permissionFlag(d.canView),
    canExecute: permissionFlag(d.canExecute),
    canEdit: permissionFlag(d.canEdit),
  };
}
export function facade(value: unknown): ManagementFacade {
  const d = object(value);
  return {
    workflowId: text(d.workflowId),
    command: text(d.command),
    packId: nullableText(d.packId),
    remark: nullableText(d.remark),
  };
}
export function notification(value: unknown, workflowId: string): ManagementNotification | null {
  if (value === null) return null;
  const d = object(value);
  identity(d.workflowId, workflowId);
  const eventOptions: Record<string, ManagementEventOptions> = {};
  if (d.eventOptions !== null && d.eventOptions !== undefined)
    for (const [event, value] of Object.entries(object(d.eventOptions))) {
      const options = object(value),
        mapped: ManagementEventOptions = {};
      if (options.title !== undefined) mapped.title = text(options.title);
      for (const key of ['includeRunLink', 'includeNodeOutput', 'includeFinalOutput'] as const)
        if (options[key] !== undefined) mapped[key] = bool(options[key]);
      for (const key of ['maxOutputFields', 'maxOutputValueLen'] as const)
        if (options[key] !== undefined) mapped[key] = count(options[key]);
      eventOptions[event] = mapped;
    }
  return {
    workflowId,
    robotCode: text(d.robotCode),
    hasSecret: text(d.appSecret).length > 0,
    onFailureUsers: array(d.onFailureUsers).map((v) => {
      const u = object(v);
      return { userId: text(u.userId), name: nullableText(u.name) };
    }),
    onFailureGroups: array(d.onFailureGroups).map((v) => {
      const g = object(v);
      return { openConversationId: text(g.openConversationId), name: nullableText(g.name) };
    }),
    onFailureMessageTitle: nullableText(d.onFailureMessageTitle),
    onFailureMessageIncludeRunLink: bool(d.onFailureMessageIncludeRunLink),
    notifyOn: d.notifyOn === null || d.notifyOn === undefined ? null : strings(d.notifyOn),
    eventOptions,
  };
}
export function callback(value: unknown, workflowId: string): ManagementCallback {
  const d = object(value);
  identity(d.workflowId, workflowId);
  return {
    configId: text(d.configId),
    workflowId,
    name: text(d.name),
    url: maskedUrl(text(d.url)),
    hasSecret: text(d.secret).length > 0,
    enabled: bool(d.enabled),
    notifyOn: strings(d.notifyOn),
    timeoutMs: count(d.timeoutMs),
    maxRetries: count(d.maxRetries),
    retryDelayMs: count(d.retryDelayMs),
    includeNodeOutput: bool(d.includeNodeOutput),
    createdAtMs: seconds(d.gmtCreate),
    modifiedAtMs: seconds(d.gmtModified),
  };
}
