import type { GroupView } from '@/domain/collaboration';
import type { MentionConfig, MentionItem } from '@tc-chat/ui';

const ALL_MENTION_ID = 'ALL';

/** 将协作群里的 bot 参与者转换为 @ 面板候选项（ALL 始终置顶）。 */
export function buildGroupMentionConfig(participants: GroupView['participants']): MentionConfig {
  const botItems: MentionItem[] = participants
    .filter((participant) => participant.kind === 'bot')
    .map((participant) => ({
      id: participant.actorId,
      name: participant.name || participant.actorId,
      description: '群成员',
    }));

  return {
    categories: [
      {
        key: 'bots',
        label: 'Bot 成员',
        items: [{ id: ALL_MENTION_ID, name: 'ALL', description: '提及所有 Bot' }, ...botItems],
      },
    ],
  };
}

/** 把 Sender 提交的 mention 项转换为 WS `mentions` 数组；@ALL 展开为全部 bot。 */
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
