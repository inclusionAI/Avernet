import type { GroupView } from '@/domain/collaboration';
import type { MentionConfig, MentionItem } from '@tc-chat/ui';

const ALL_MENTION_ID = 'ALL-Bots';

export interface BuildGroupMentionConfigOptions {
  excludedActorIds?: string[];
}

/** 将协作群会话成员转换为 @ 面板候选项（ALL 仅展开 Bot，当前用户不展示）。 */
export function buildGroupMentionConfig(
  participants: GroupView['participants'],
  options: BuildGroupMentionConfigOptions = {},
): MentionConfig {
  const excludedActorIds = new Set(options.excludedActorIds ?? []);
  const memberItems: MentionItem[] = participants
    .filter((participant) => participant.actorId && !excludedActorIds.has(participant.actorId))
    .map((participant) => ({
      id: participant.actorId,
      name: participant.name || participant.actorId,
      description: participant.kind === 'human' ? '用户' : 'Bot',
      ...(participant.avatarUrl ? { avatar: participant.avatarUrl } : {}),
      ...(participant.kind === 'human' && participant.mode === 'absent' ? { disabled: true } : {}),
    }));

  return {
    categories: [
      {
        key: 'members',
        label: '会话成员',
        items: [{ id: ALL_MENTION_ID, name: 'ALL-Bots', description: '提及所有 Bot' }, ...memberItems],
      },
    ],
  };
}

/** 把 Sender 提交的 mention 项转换为 WS `mentions` 数组；@ALL-Bots 展开为全部 bot。 */
export function expandMentionIds(
  mentionItems: MentionItem[],
  participants: GroupView['participants'],
): string[] | undefined {
  const ids = mentionItems.map((item) => item.id);
  if (ids.length === 0) return undefined;

  if (ids.includes(ALL_MENTION_ID)) {
    const botIds = participants
      .filter((participant) => participant.kind === 'bot')
      .map((participant) => participant.actorId)
      .filter((actorId) => actorId.length > 0);
    return botIds.length > 0 ? [...new Set(botIds)] : undefined;
  }

  return [...new Set(ids)];
}

/** 仅 @ human 时不期待 Bot 流式回复；混合提及或普通消息仍显示等待态。 */
export function isHumanOnlyMention(mentionIds: string[] | undefined, participants: GroupView['participants']): boolean {
  if (!mentionIds?.length) return false;
  const humanIds = new Set(
    participants.filter((participant) => participant.kind === 'human').map((participant) => participant.actorId),
  );
  return mentionIds.every((mentionId) => humanIds.has(mentionId));
}
