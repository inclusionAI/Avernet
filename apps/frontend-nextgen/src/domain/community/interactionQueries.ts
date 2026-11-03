import { MAX_INTERACTION_SUMMARY_BATCH } from '@/domain/community/constants';
import type {
  CommunityAuthor,
  CommunityReply,
  CommunityTopic,
  ForumActorRef,
  ForumTargetRef,
  InteractionSummaryQuery,
} from '@/domain/community/types';

export function forumAuthorRef(author: CommunityAuthor): ForumActorRef {
  return {
    actorType: author.type === 'bot' ? 'BOT' : 'HUMAN',
    id: author.id,
  };
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
      target: {
        targetType: topic.author.type === 'bot' ? 'BOT' : 'HUMAN',
        id: topic.author.id,
      },
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
    add({
      actor: viewer,
      target: { targetType: author.type === 'bot' ? 'BOT' : 'HUMAN', id: author.id },
    });
  });
  visiblePosts(replies).forEach((reply) => {
    const replyTarget: ForumTargetRef = { targetType: 'POST', id: reply.id };
    add({ target: replyTarget });
    add({ actor: viewer, target: replyTarget });
  });

  return [...queries.values()].slice(0, MAX_INTERACTION_SUMMARY_BATCH);
}
