import { MAX_INTERACTION_SUMMARY_BATCH } from '@/domain/community/constants';
import type {
  CommunityAuthor,
  CommunityReply,
  CommunityTopic,
  ForumActorRef,
  ForumTargetRef,
  InteractionSummaryQuery,
} from '@/domain/community/types';

function botIdentity(author: CommunityAuthor): { id: string; idAlias?: string } | undefined {
  const trimmedId = author.id.trim();
  const botUuid = author.botUuid?.trim() || (trimmedId.includes(':') ? trimmedId : undefined);
  const botId = author.botId?.trim() || (trimmedId ? trimmedId.split(':')[0] : undefined);
  if (!botUuid || !botId) {
    // A bare legacy Bot id has no owner suffix and cannot satisfy the query.
    return undefined;
  }
  return botId === 'default'
    ? { id: botUuid }
    : { id: botUuid, idAlias: botId };
}

export function forumAuthorRef(author: CommunityAuthor): ForumActorRef {
  if (author.type === 'bot') {
    const identity = botIdentity(author);
    if (identity) return { actorType: 'BOT', ...identity };
    throw new Error('BOT 作者缺少互动身份，无法查询统计');
  }
  return { actorType: 'HUMAN', id: author.id };
}

export function summaryTargetRef(author: CommunityAuthor): ForumTargetRef {
  if (author.type === 'bot') {
    const identity = botIdentity(author);
    if (!identity) throw new Error('default Bot 缺少工号后缀，无法查询互动统计');
    return { targetType: 'BOT', ...identity };
  }
  return { targetType: 'HUMAN', id: author.id };
}

export function interactionTargetRef(author: CommunityAuthor): ForumTargetRef {
  if (author.type === 'bot') {
    const identity = botIdentity(author);
    if (!identity?.id.includes(':')) {
      throw new Error('BOT 缺少带工号后缀的 bot_uuid，无法记录互动');
    }
    return { targetType: 'BOT', ...identity };
  }
  return { targetType: 'HUMAN', id: author.id };
}

function visiblePosts(replies: CommunityReply[]): CommunityReply[] {
  const result: CommunityReply[] = [];
  replies.forEach((reply) => {
    result.push(reply);
    result.push(...(reply.childReplies ?? []));
  });
  return result;
}

export function buildTopicListSummaryQueries(
  topics: CommunityTopic[],
  viewer?: ForumActorRef,
): InteractionSummaryQuery[] {
  const queries = new Map<string, InteractionSummaryQuery>();
  const add = (query: InteractionSummaryQuery) => {
    const key = [
      query.actor ? `${query.actor.actorType}:${query.actor.id}` : '-',
      query.target ? `${query.target.targetType}:${query.target.id}` : '-',
    ].join('|');
    queries.set(key, query);
  };

  topics.forEach((topic) => {
    add({ target: { targetType: 'TOPIC', id: topic.id } });
    if (!viewer) return;

    const actor = forumAuthorRef(topic.author);
    add({ actor });
    add({
      actor: viewer,
      target: summaryTargetRef(topic.author),
    });
  });

  if (queries.size > MAX_INTERACTION_SUMMARY_BATCH) {
    return [...queries.values()].slice(0, MAX_INTERACTION_SUMMARY_BATCH);
  }
  return [...queries.values()];
}

export function buildTopicSummaryQueries(options: {
  topic: CommunityTopic;
  replies: CommunityReply[];
  viewer: ForumActorRef;
}): InteractionSummaryQuery[] {
  const { topic, replies, viewer } = options;
  const queries = new Map<string, InteractionSummaryQuery>();
  const add = (query: InteractionSummaryQuery) => {
    const key = [
      query.actor ? `${query.actor.actorType}:${query.actor.id}` : '-',
      query.target ? `${query.target.targetType}:${query.target.id}` : '-',
    ].join('|');
    queries.set(key, query);
  };

  const topicTarget: ForumTargetRef = { targetType: 'TOPIC', id: topic.id };
  add({ target: topicTarget });
  add({ actor: viewer, target: topicTarget });

  const authors = [topic.author, ...visiblePosts(replies).map((reply) => reply.author)];
  const uniqueAuthors = new Map<string, CommunityAuthor>();
  authors.forEach((author) => uniqueAuthors.set(`${author.type}:${author.id}`, author));
  uniqueAuthors.forEach((author) => {
    add({ actor: forumAuthorRef(author) });
    add({ actor: viewer, target: summaryTargetRef(author) });
  });
  visiblePosts(replies).forEach((reply) => {
    const replyTarget: ForumTargetRef = { targetType: 'POST', id: reply.id };
    add({ target: replyTarget });
    add({ actor: viewer, target: replyTarget });
  });

  return [...queries.values()].slice(0, MAX_INTERACTION_SUMMARY_BATCH);
}
