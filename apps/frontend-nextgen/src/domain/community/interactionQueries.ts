import { MAX_INTERACTION_SUMMARY_BATCH } from '@/domain/community/constants';
import type {
  CommunityAuthor,
  CommunityReply,
  CommunityTopic,
  ForumActorRef,
  ForumTargetRef,
  InteractionSummaryQuery,
} from '@/domain/community/types';

type BotIdentity = { id: string; idAlias?: string };

function scopedBotId(value: string | undefined): string | undefined {
  const normalized = value?.trim();
  if (!normalized || !normalized.includes(':')) return undefined;
  const botId = normalized.split(':')[0];
  if (!botId) return undefined;
  return normalized;
}

function botIdentity(author: CommunityAuthor): BotIdentity | undefined {
  // Identity metadata is best-effort on the read side. Prefer the explicit
  // owner-scoped uuid, then a scoped author id; a bare historical id alone
  // cannot be used by the summary API, so such author queries are skipped.
  const uuid = scopedBotId(author.botUuid) ?? scopedBotId(author.id);
  if (!uuid) return undefined;
  const botId = uuid.split(':')[0];
  return botId === 'default' ? { id: uuid } : { id: uuid, idAlias: botId };
}

export function forumAuthorRef(author: CommunityAuthor): ForumActorRef | undefined {
  if (author.type === 'bot') {
    const identity = botIdentity(author);
    if (!identity) return undefined;
    return { actorType: 'BOT', ...identity };
  }
  return { actorType: 'HUMAN', id: author.id };
}

export function summaryTargetRef(author: CommunityAuthor): ForumTargetRef | undefined {
  if (author.type === 'bot') {
    const identity = botIdentity(author);
    if (!identity) return undefined;
    return { targetType: 'BOT', ...identity };
  }
  return { targetType: 'HUMAN', id: author.id };
}

export function interactionTargetRef(author: CommunityAuthor): ForumTargetRef {
  if (author.type === 'bot') {
    const identity = botIdentity(author);
    if (!identity) {
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
    const target = summaryTargetRef(topic.author);
    if (actor) add({ actor });
    if (target) {
      add({
        actor: viewer,
        target,
      });
    }
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
    const actor = forumAuthorRef(author);
    const target = summaryTargetRef(author);
    if (actor) add({ actor });
    if (target) {
      add({ actor: viewer, target });
    }
  });
  visiblePosts(replies).forEach((reply) => {
    const replyTarget: ForumTargetRef = { targetType: 'POST', id: reply.id };
    add({ target: replyTarget });
    add({ actor: viewer, target: replyTarget });
  });

  return [...queries.values()].slice(0, MAX_INTERACTION_SUMMARY_BATCH);
}
