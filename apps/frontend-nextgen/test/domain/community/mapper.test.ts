import { mapCommunityReplyDto, mapCommunityTopicDto } from '../../../src/domain/community/mapper';

describe('community mapper', () => {
  test('maps a single topic model without task/type semantics', () => {
    const topic = mapCommunityTopicDto({
      topic_id: 'topic-1',
      title: '如何让 Bot 更好地参与协作？',
      body: '分享你的实践。',
      status: 'OPEN',
      author: { actor_type: 'HUMAN', actor_id: 'user-1', display_name: '顾客' },
      reply_count: 2,
      latest_activity_at: '2026-10-06T10:00:00Z',
      created_at: '2026-10-05T09:00:00Z',
      is_mine: true,
      can_close: true,
      topic_type: 'POLL',
    });

    expect(topic).toEqual({
      id: 'topic-1',
      title: '如何让 Bot 更好地参与协作？',
      body: '分享你的实践。',
      status: 'open',
      author: { type: 'human', id: 'user-1', displayName: '顾客', avatarUrl: undefined },
      replyCount: 2,
      latestActivityAt: '2026-10-06T10:00:00Z',
      createdAt: '2026-10-05T09:00:00Z',
      isMine: true,
      canClose: true,
    });
    expect(topic).not.toHaveProperty('topicType');
  });

  test('maps bot replies and normalizes unknown topic status to closed', () => {
    expect(
      mapCommunityReplyDto({
        post_id: 'post-1',
        topic_id: 'topic-1',
        body: '我会先澄清目标。',
        author: { actor_type: 'BOT', actor_id: 'bot-1', display_name: '产品助手' },
        created_at: '2026-10-06T11:00:00Z',
      }),
    ).toMatchObject({ author: { type: 'bot', displayName: '产品助手' } });

    expect(
      mapCommunityTopicDto({
        topic_id: 'locked',
        title: '锁定主题',
        body: '',
        status: 'LOCKED',
        author: { actor_type: 'HUMAN', actor_id: 'u', display_name: '用户' },
        reply_count: 0,
        latest_activity_at: '',
        created_at: '',
        is_mine: false,
        can_close: false,
      }).status,
    ).toBe('closed');
  });
});

test('maps the real backend flattened BBS DTO (no nested author, no reply count/name/avatar)', () => {
  const topic = mapCommunityTopicDto({
    topic_id: 'real-1',
    title: '真实主题',
    body_preview: '正文前 500 字',
    body_truncated: true,
    status: 'OPEN',
    topic_type: 'DISCUSSION',
    author_type: 'HUMAN',
    author_id: '900003',
    updated_at: '2026-10-06T12:00:00Z',
    created_at: '2026-10-05T08:00:00Z',
  });

  expect(topic.id).toBe('real-1');
  expect(topic.body).toBe('正文前 500 字');
  expect(topic.latestActivityAt).toBe('2026-10-06T12:00:00Z');
  expect(topic.author).toEqual({ type: 'human', id: '900003', displayName: '900003', avatarUrl: undefined });
  expect(topic.replyCount).toBeUndefined();
  expect(topic.isMine).toBe(false);
  expect(topic.canClose).toBe(false);
  expect(topic).not.toHaveProperty('topicType');
});

test('maps a real backend reply with flattened author', () => {
  const reply = mapCommunityReplyDto({
    post_id: 'post-real',
    topic_id: 'real-1',
    body: '一条来自真后端的回复',
    author_type: 'BOT',
    author_id: 'product-helper',
    created_at: '2026-10-06T10:00:00Z',
    updated_at: '2026-10-06T10:05:00Z',
  });
  expect(reply).toMatchObject({
    id: 'post-real',
    topicId: 'real-1',
    author: { type: 'bot', id: 'product-helper', displayName: 'product-helper' },
  });
});

test('falls back to anonymous when author has neither display_name nor actor_id', () => {
  expect(mapCommunityTopicDto({ topic_id: 't', title: 'T', body: 'b', status: 'OPEN' }).author.displayName).toBe(
    '匿名',
  );
});
