import {
  mapCommunityReplyDto,
  mapCommunityTopicDto,
  mapPersonalCenterActivityDto,
} from '@/domain/community/mapper';
import { enrichOwnership } from '@/domain/community/ownership';

describe('community mapper', () => {
  test('maps topic metadata and page totals for the BBS list item', () => {
    const topic = mapCommunityTopicDto({
      topic_id: 'topic-1',
      title: '如何让 Bot 更好地参与协作？',
      body: '分享你的实践。',
      status: 'OPEN',
      author: { actor_type: 'HUMAN', actor_id: 'user-1', display_name: '顾客' },
      reply_count: 2,
      like_count: 5,
      latest_activity_at: '2026-10-06T10:00:00Z',
      created_at: '2026-10-05T09:00:00Z',
      is_mine: true,
      can_close: true,
      topic_type: 'POLL',
    });

    expect(topic).toMatchObject({
      id: 'topic-1',
      title: '如何让 Bot 更好地参与协作？',
      body: '分享你的实践。',
      status: 'open',
      author: { type: 'human', id: 'user-1', displayName: '顾客', avatarUrl: undefined },
      replyCount: 2,
      likeCount: 5,
      latestActivityAt: '2026-10-06T10:00:00Z',
      createdAt: '2026-10-05T09:00:00Z',
      isMine: true,
      canClose: true,
      canEdit: false,
      canDelete: false,
      topicType: 'poll',
    });
    expect(topic.topicConfig).toBeUndefined();
    expect(topic.analysis).toBeUndefined();

    const owned = enrichOwnership(topic, 'user-1');
    expect(owned).toMatchObject({
      isMine: true,
      canClose: true,
      canEdit: true,
      canDelete: true,
    });
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

test('maps topic type and structured topic_config', () => {
  const topic = mapCommunityTopicDto({
    topic_id: 'topic-1',
    title: '投票',
    body: '请选择',
    status: 'OPEN',
    topic_type: 'POLL',
    topic_config: {
      poll: {
        selection: 'multi',
        options: [' 方案 A ', '', '方案 B'],
        deadline_at: '2026-10-20T12:00:00Z',
      },
    },
    author_type: 'HUMAN',
    author_id: 'u1',
    created_at: '2026-10-01T00:00:00Z',
  });

  expect(topic.topicType).toBe('poll');
  expect(topic.topicConfig).toEqual({
    poll: {
      selection: 'multi',
      options: ['方案 A', '方案 B'],
      deadlineAt: '2026-10-20T12:00:00Z',
    },
  });
});

test('maps the topic_config and analysis containers', () => {
  const topic = mapCommunityTopicDto({
    topic_id: 'topic-1',
    title: '投票',
    body: '请选择',
    status: 'OPEN',
    topic_type: 'POLL',
    topic_config: { poll: { selection: 'single', options: ['方案 A', '方案 B'] } },
    analysis: {
      summary: { content: ' 主题要求参与者选择一种方案并说明理由。' },
      tags: [' AI治理 ', '', '投票'],
      poll_stats: {
        total_votes: 3,
        participant_count: 3,
        updated_at: '2026-10-19T10:00:00Z',
        by_option: [
          { index: 0, votes: 2, percent: 67 },
          { index: 1, votes: 1, percent: 33 },
          { index: 2, votes: 0, percent: 0 },
        ],
      },
    },
    author: { actor_type: 'HUMAN', actor_id: 'user-1', display_name: '顾客' },
    created_at: '2026-10-01T00:00:00Z',
  });

  expect(topic.analysis).toEqual({
    summary: { content: '主题要求参与者选择一种方案并说明理由。' },
    tags: ['AI治理', '投票'],
    pollStats: {
      totalVotes: 3,
      participantCount: 3,
      options: [
        { label: '方案 A', votes: 2, percent: 67 },
        { label: '方案 B', votes: 1, percent: 33 },
      ],
      updatedAt: '2026-10-19T10:00:00Z',
    },
  });
});

test('defaults legacy topics to discussion and omits poll', () => {
  const topic = mapCommunityTopicDto({ topic_id: 'topic-1', title: '讨论', status: 'OPEN' });

  expect(topic.topicType).toBe('discussion');
  expect(topic.topicConfig).toBeUndefined();
  expect(topic.analysis).toBeUndefined();
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
  expect(topic.topicType).toBe('discussion');
  expect(topic.topicConfig).toBeUndefined();
  expect(topic.analysis).toBeUndefined();
  expect(topic.author).toEqual({ type: 'human', id: '900003', displayName: '900003', avatarUrl: undefined });
  expect(topic.replyCount).toBeUndefined();
  expect(topic.likeCount).toBeUndefined();
  expect(topic.isMine).toBe(false);
  expect(topic.canClose).toBe(false);
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
    helpful: false,
  });
});

test('maps flat reply reference fields without adding a tree edge', () => {
  const reply = mapCommunityReplyDto({
    post_id: 'post-2',
    topic_id: 'topic-1',
    body: '继续讨论',
    floor_no: 3,
    helpful: true,
    reply_to_post_id: 'post-1',
    reply_to_floor_no: 1,
    author_type: 'HUMAN',
    author_id: 'user-1',
    display_name: '顾客',
    created_at: '2026-10-16T08:00:00Z',
  });

  expect(reply.replyToPostId).toBe('post-1');
  expect(reply.replyToFloorNo).toBe(1);
  expect(reply.floorNo).toBe(3);
  expect(reply.helpful).toBe(true);
  expect(reply).not.toHaveProperty('children');
  expect(reply).not.toHaveProperty('replies');
});

test('maps structured POLL vote selections on replies', () => {
  const reply = mapCommunityReplyDto({
    post_id: 'post-vote',
    topic_id: 'topic-1',
    body: '投票选择：方案 A',
    author_type: 'HUMAN',
    author_id: 'user-1',
    display_name: '顾客',
    created_at: '2026-10-20T08:00:00Z',
    poll_selection: { option_indices: [0, 1] },
  });

  expect(reply.pollSelection).toEqual([0, 1]);
});

test('maps first-level replies with their two-level child reply group', () => {
  const reply = mapCommunityReplyDto({
    post_id: 'post-1',
    topic_id: 'topic-1',
    body: '一级楼层',
    author_type: 'HUMAN',
    author_id: 'user-1',
    display_name: '顾客',
    created_at: '2026-10-19T08:00:00Z',
    children: [
      {
        post_id: 'post-2',
        topic_id: 'topic-1',
        body: '楼中楼回复',
        reply_to_post_id: 'post-1',
        reply_to_floor_no: 1,
        author_type: 'BOT',
        author_id: 'bot-1',
        display_name: '协作助手',
        created_at: '2026-10-19T08:10:00Z',
      },
    ],
  });

  expect(reply.childReplies).toHaveLength(1);
  expect(reply.childReplies?.[0]).toMatchObject({
    id: 'post-2',
    replyToPostId: 'post-1',
    replyToFloorNo: 1,
    helpful: false,
    author: { type: 'bot', displayName: '协作助手' },
  });
  expect(reply.childReplies?.[0]).not.toHaveProperty('children');
});

test('falls back to anonymous when author has neither display_name nor actor_id', () => {
  expect(mapCommunityTopicDto({ topic_id: 't', title: 'T', body: 'b', status: 'OPEN' }).author.displayName).toBe(
    '匿名',
  );
});


test('maps SHARE type without topic configuration', () => {
  const topic = mapCommunityTopicDto({
    topic_id: 'share-1',
    title: '分享经验',
    body: '正文',
    status: 'OPEN',
    topic_type: 'SHARE',
  });

  expect(topic.topicType).toBe('share');
  expect(topic.topicConfig).toBeUndefined();
});

test('maps HELP topics while ignoring legacy expected-time config', () => {
  const mapped = mapCommunityTopicDto({
    topic_id: 'help-1',
    title: '求助',
    status: 'OPEN',
    topic_type: 'HELP',
    topic_config: { help: { expected_time: ' 希望本周内 ' } },
  });

  expect(mapped.topicType).toBe('help');
  expect(mapped.topicConfig).toBeUndefined();
});


test('maps personal-center topic and nested-reply contexts', () => {
  const activity = mapPersonalCenterActivityDto({
    activity_type: 'REPLIED',
    actor_type: 'HUMAN',
    actor_id: 'user-1',
    target_type: 'POST',
    target_id: 'post-parent',
    topic_id: 'topic-1',
    post_id: 'post-child',
    happened_at: '2026-10-29T08:00:00Z',
    body_preview: '我的楼中楼回复',
    topic_context: {
      title: '讨论主题',
      body_preview: '正文预览',
      topic_type: 'DISCUSSION',
      author_type: 'BOT',
      author_id: 'bot-1',
      author_display_name: '协作助手',
      reply_count: 3,
      like_count: 4,
      latest_reply_at: '2026-10-29T09:00:00Z',
    },
    replied_to_post: {
      post_id: 'post-parent',
      author_type: 'BOT',
      author_id: 'bot-2',
      author_display_name: '风控分析 Bot',
      body_preview: '原始回复',
    },
  });

  expect(activity?.topicContext).toMatchObject({
    title: '讨论主题',
    bodyPreview: '正文预览',
    topicType: 'discussion',
    author: { actorType: 'BOT', id: 'bot-1' },
    authorDisplayName: '协作助手',
    replyCount: 3,
    likeCount: 4,
    latestReplyAt: '2026-10-29T09:00:00Z',
  });
  expect(activity?.repliedToPost).toMatchObject({
    postId: 'post-parent',
    author: { actorType: 'BOT', id: 'bot-2' },
    authorDisplayName: '风控分析 Bot',
    bodyPreview: '原始回复',
  });
});
