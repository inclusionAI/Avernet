import type { CommunityReply } from '@/domain/community/types';

export function formatDateTime(value: string) {
  if (!value) return '时间未知';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '时间未知';
  const pad = (input: number) => String(input).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}`;
}

export interface CommunityThreadReply {
  reply: CommunityReply;
  targetAuthorName: string;
  targetPostId?: string;
}

export interface CommunityFloorReplyGroup {
  reply: CommunityReply;
  floorNo: number;
  children: CommunityThreadReply[];
}

export type ReplyElementRegistrar = (postId?: string) => (instance: HTMLElement | null) => void;

function indexReplies(replies: CommunityReply[]) {
  const replyById = new Map<string, CommunityReply>();
  replies.forEach((reply) => {
    replyById.set(reply.id, reply);
    reply.childReplies?.forEach((child) => replyById.set(child.id, child));
  });
  return replyById;
}

/**
 * Normalizes nested API payloads and the older flat page shape. The nested
 * payload is canonical for 楼中楼: `reply.childReplies` already contains every
 * descendant of that first-level floor. The flat path only remains as a
 * compatibility fallback for tests and clients provisioned before v2.
 */
export function buildReplyGroups(
  replies: CommunityReply[],
  firstFloorNo: number,
): CommunityFloorReplyGroup[] {
  const replyById = indexReplies(replies);
  const targetName = (postId?: string) =>
    postId ? replyById.get(postId)?.author.displayName || '原回复' : '原回复';

  if (replies.some((reply) => reply.childReplies?.length)) {
    return replies.map((reply, index) => ({
      reply,
      floorNo: reply.floorNo ?? firstFloorNo + index,
      children: (reply.childReplies ?? []).map((child) => ({
        reply: child,
        targetAuthorName: targetName(child.replyToPostId),
        targetPostId: child.replyToPostId,
      })),
    }));
  }

  const groups: CommunityFloorReplyGroup[] = [];
  const groupByReplyId = new Map<string, CommunityFloorReplyGroup>();

  replies.forEach((reply, index) => {
    const target = reply.replyToPostId ? replyById.get(reply.replyToPostId) : undefined;
    const targetGroup = target ? groupByReplyId.get(target.id) : undefined;

    if (target && targetGroup) {
      targetGroup.children.push({
        reply,
        targetAuthorName: target.author.displayName,
        targetPostId: target.id,
      });
      groupByReplyId.set(reply.id, targetGroup);
      return;
    }

    const group: CommunityFloorReplyGroup = {
      reply,
      floorNo: reply.floorNo ?? firstFloorNo + index,
      children: [],
    };
    groups.push(group);
    groupByReplyId.set(reply.id, group);
  });

  return groups;
}
