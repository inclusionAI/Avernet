import type { BotChatDetail, BotChatPage, BotChatRelationScope, BotChatSummary } from '@/domain/botChats';

export interface BotChatRelationOption {
  value: BotChatRelationScope;
  label: string;
  valueText: string;
  disabledReason?: string;
}

export function getBotChatRelationOptions(detail: BotChatDetail): BotChatRelationOption[] {
  return [
    {
      value: 'session',
      label: '按 sessionID',
      valueText: detail.sessionId || detail.sessionKey || '-',
      disabledReason:
        detail.sessionId || detail.sessionKey ? undefined : '当前 Trace 无 sessionID / sessionKey，无法按该维度查询',
    },
    {
      value: 'task',
      label: '按任务ID',
      valueText: detail.bizTaskId || '-',
      disabledReason: detail.bizTaskId ? undefined : '当前 Trace 无任务ID，无法按该维度查询',
    },
    {
      value: 'group',
      label: '按群ID',
      valueText: detail.groupId || '-',
      disabledReason: detail.groupId ? undefined : '当前 Trace 无群ID，无法按该维度查询',
    },
  ];
}

export function resolveBotChatRelationScope(
  detail: BotChatDetail,
  preferred: BotChatRelationScope = 'session',
): BotChatRelationScope {
  const options = getBotChatRelationOptions(detail);
  if (!options.find((item) => item.value === preferred)?.disabledReason) return preferred;
  return options.find((item) => !item.disabledReason)?.value ?? preferred;
}

export interface BotChatRelatedGroup {
  key: string;
  label: string;
  items: BotChatSummary[];
}

export function groupBotChatRelatedTraces(
  page: BotChatPage | undefined,
  scope: BotChatRelationScope,
): BotChatRelatedGroup[] {
  if (!page?.items.length) return [];
  if (scope !== 'task') {
    return [{ key: 'all', label: '', items: page.items }];
  }

  const groups = new Map<string, BotChatSummary[]>();
  page.items.forEach((item) => {
    const key = item.sessionId || item.sessionKey || '__no_session__';
    groups.set(key, [...(groups.get(key) ?? []), item]);
  });

  return [...groups.entries()]
    .map(([key, items]) => ({
      key,
      label: key === '__no_session__' ? '未关联 Session' : key,
      items: [...items].sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp)),
    }))
    .sort((a, b) => {
      const latestA = Math.max(...a.items.map((item) => Date.parse(item.timestamp)));
      const latestB = Math.max(...b.items.map((item) => Date.parse(item.timestamp)));
      return latestB - latestA;
    });
}
