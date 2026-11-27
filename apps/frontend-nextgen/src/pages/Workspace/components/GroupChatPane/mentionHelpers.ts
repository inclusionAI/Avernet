import type { GroupKind, ParticipantView } from '@/domain/collaboration';
import type { MentionConfig, MentionItem } from '@tc-chat/ui';
import { renderGroupMentionItem } from './GroupMentionItem';

const ALL_MENTION_ID = 'ALL-Bots';

export interface BuildGroupMentionConfigOptions {
  excludedActorIds?: string[];
  groupKind?: GroupKind;
}

/** 任务协作群只允许直接点名主节点 Bot；Human 仍按参与状态决定可选性。 */
function canMention(participant: ParticipantView, groupKind: GroupKind | undefined): boolean {
  if (participant.kind === 'human') return participant.mode !== 'absent';
  return groupKind !== 'task_master_slave' || participant.role === 'manager';
}

/** 将会话成员转换为 @ 候选；任务协作群不提供 ALL-Bots。 */
export function buildGroupMentionConfig(
  participants: ParticipantView[],
  options: BuildGroupMentionConfigOptions = {},
): MentionConfig {
  const { groupKind, excludedActorIds: excluded = [] } = options;
  const excludedActorIds = new Set(excluded);
  const memberItems: MentionItem[] = participants
    .filter((participant) => participant.actorId && !excludedActorIds.has(participant.actorId))
    .map((participant) => ({
      id: participant.actorId,
      name: participant.name || participant.actorId,
      description: participant.kind === 'human' ? '用户' : 'Bot',
      ...(participant.avatarUrl ? { avatar: participant.avatarUrl } : {}),
      disabled: !canMention(participant, groupKind),
      data: { kind: participant.kind, role: groupKind === 'task_master_slave' ? participant.role : undefined },
    }));

  return {
    categories: [
      {
        key: 'members',
        label: '会话成员',
        renderItem: renderGroupMentionItem,
        items: groupKind === 'task_master_slave'
          ? memberItems
          : [{ id: ALL_MENTION_ID, name: 'ALL-Bots', description: '提及所有 Bot' }, ...memberItems],
      },
    ],
  };
}

/** 把 Sender 提交的 mention 项转换为 WS `mentions`，再次按当前会话成员校验任务协作目标。 */
export function expandMentionIds(
  mentionItems: MentionItem[],
  participants: ParticipantView[],
  groupKind?: GroupKind,
): string[] | undefined {
  const eligible = groupKind === 'task_master_slave'
    ? new Set(participants.filter((participant) => canMention(participant, groupKind)).map((participant) => participant.actorId))
    : undefined;
  const ids = mentionItems.map((item) => item.id);
  const targets = ids.includes(ALL_MENTION_ID)
    ? participants.filter((participant) => participant.kind === 'bot').map((participant) => participant.actorId)
    : ids;
  const unique = [...new Set(targets.filter((id) => id && (!eligible || eligible.has(id))))];
  return unique.length ? unique : undefined;
}

/** 仅 @ human 时不期待 Bot 流式回复；混合提及或普通消息仍显示等待态。 */
export function isHumanOnlyMention(mentionIds: string[] | undefined, participants: ParticipantView[]): boolean {
  if (!mentionIds?.length) return false;
  const humanIds = new Set(
    participants.filter((participant) => participant.kind === 'human').map((participant) => participant.actorId),
  );
  return mentionIds.every((mentionId) => humanIds.has(mentionId));
}
