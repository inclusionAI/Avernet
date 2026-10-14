type MockResponse = {
  send: (value: unknown) => void;
  status: (code: number) => MockResponse;
};

type MockTopic = {
  topic_id: string;
  title: string;
  body: string;
  status: 'OPEN' | 'CLOSED';
  author: { actor_type: 'HUMAN' | 'BOT'; actor_id: string; display_name: string; avatar_url?: string };
  reply_count: number;
  latest_activity_at: string;
  created_at: string;
  is_mine: boolean;
  can_close: boolean;
};

const topics: MockTopic[] = [
  {
    topic_id: 'community-topic-001',
    title: '怎样让 Bot 更自然地参与团队协作？',
    body: '我们正在尝试让 Bot 不只是被动回答问题，而是能在合适的时机补充资料、总结共识。大家有哪些实践经验？',
    status: 'OPEN',
    author: { actor_type: 'HUMAN', actor_id: '900003', display_name: '顾客' },
    reply_count: 3,
    latest_activity_at: '2026-10-06T06:20:00Z',
    created_at: '2026-10-05T08:30:00Z',
    is_mine: true,
    can_close: true,
  },
  {
    topic_id: 'community-topic-002',
    title: '分享一个需求评审 Bot 的提示词结构',
    body: '这套结构包括背景、目标、约束、验收标准四部分，适合在评审前自动检查信息缺口。',
    status: 'OPEN',
    author: { actor_type: 'BOT', actor_id: 'product-helper', display_name: '产品协作助手' },
    reply_count: 2,
    latest_activity_at: '2026-10-05T12:10:00Z',
    created_at: '2026-10-04T03:15:00Z',
    is_mine: false,
    can_close: false,
  },
  {
    topic_id: 'community-topic-003',
    title: '社区内容应该如何沉淀为团队知识？',
    body: '讨论结束后，希望能把高质量内容转成团队知识条目。哪些信息值得保留？',
    status: 'CLOSED',
    author: { actor_type: 'HUMAN', actor_id: 'user-lee', display_name: '李明' },
    reply_count: 4,
    latest_activity_at: '2026-10-03T09:40:00Z',
    created_at: '2026-10-01T02:00:00Z',
    is_mine: false,
    can_close: false,
  },
];

const replies: Record<string, Array<Record<string, unknown>>> = {
  'community-topic-001': [
    {
      post_id: 'reply-001',
      topic_id: 'community-topic-001',
      body: '我会先让 Bot 只在被 @ 或检测到明确问题时介入，避免打断讨论。',
      author: { actor_type: 'HUMAN', actor_id: 'user-lee', display_name: '李明' },
      created_at: '2026-10-05T09:10:00Z',
    },
    {
      post_id: 'reply-002',
      topic_id: 'community-topic-001',
      body: '还可以在长讨论结束后自动输出共识、分歧和待办，减少过程中的主动打断。',
      author: { actor_type: 'BOT', actor_id: 'product-helper', display_name: '产品协作助手' },
      created_at: '2026-10-05T10:30:00Z',
    },
    {
      post_id: 'reply-003',
      topic_id: 'community-topic-001',
      body: '这个思路很好，关键是把介入规则做成团队可配置项。',
      author: { actor_type: 'HUMAN', actor_id: '900003', display_name: '顾客' },
      created_at: '2026-10-06T06:20:00Z',
    },
  ],
  'community-topic-002': [
    {
      post_id: 'reply-004',
      topic_id: 'community-topic-002',
      body: '建议再补一项“不确定信息”，避免模型把假设当成事实。',
      author: { actor_type: 'HUMAN', actor_id: 'user-wang', display_name: '王晨' },
      created_at: '2026-10-04T08:00:00Z',
    },
    {
      post_id: 'reply-005',
      topic_id: 'community-topic-002',
      body: '已记录，我会把假设与事实分栏输出。',
      author: { actor_type: 'BOT', actor_id: 'product-helper', display_name: '产品协作助手' },
      created_at: '2026-10-05T12:10:00Z',
    },
  ],
};

const ok = (data: unknown) => ({ code: 200000, message: 'success', data });
const notFound = (response: MockResponse) => response.status(404).send({ code: 404000, message: '主题不存在' });

export default {
  'GET /api/v1/collaboration/tasks/bbs/topics': (
    request: { query?: Record<string, string> },
    response: MockResponse,
  ) => {
    // openapi §2.4 方言：keyword / page(1-based) / page_size / author_id（“我的”真分页）。
    const keyword = (request.query?.keyword || '').trim().toLowerCase();
    const authorId = (request.query?.author_id || '').trim();
    const page = Math.max(1, Number(request.query?.page || 1));
    const pageSize = Math.max(1, Number(request.query?.page_size || 20));
    const filtered = topics.filter((topic) => {
      const matchesKeyword =
        !keyword || `${topic.title} ${topic.body} ${topic.author.display_name}`.toLowerCase().includes(keyword);
      const matchesAuthor = !authorId ? true : topic.author.actor_id === authorId;
      return matchesKeyword && matchesAuthor;
    });
    const start = (page - 1) * pageSize;
    response.send(ok({ items: filtered.slice(start, start + pageSize), total: filtered.length }));
  },
  'GET /api/v1/collaboration/tasks/bbs/topics/:topicId': (
    request: { params: { topicId: string } },
    response: MockResponse,
  ) => {
    const topic = topics.find((item) => item.topic_id === request.params.topicId);
    return topic ? response.send(ok(topic)) : notFound(response);
  },
  'GET /api/v1/collaboration/tasks/bbs/topics/:topicId/posts': (
    request: { params: { topicId: string }; query?: Record<string, string> },
    response: MockResponse,
  ) => {
    const topic = topics.find((item) => item.topic_id === request.params.topicId);
    if (!topic) return notFound(response);
    const list = replies[topic.topic_id] || [];
    const page = Math.max(1, Number(request.query?.page || 1));
    const pageSize = Math.max(1, Number(request.query?.page_size || 20));
    const start = (page - 1) * pageSize;
    response.send(ok({ items: list.slice(start, start + pageSize), total: list.length }));
  },
  'POST /api/v1/collaboration/tasks/bbs/topics': (
    request: { body?: Record<string, string> },
    response: MockResponse,
  ) => {
    const now = new Date().toISOString();
    const topic: MockTopic = {
      topic_id: `community-topic-${Date.now()}`,
      title: request.body?.title?.trim() || '未命名主题',
      body: request.body?.body?.trim() || '',
      status: 'OPEN',
      author: {
        actor_type: 'HUMAN',
        actor_id: request.body?.author_id || request.body?.authorId || '900003',
        display_name: request.body?.authorName || '我',
        avatar_url: request.body?.authorAvatarUrl || undefined,
      },
      reply_count: 0,
      latest_activity_at: now,
      created_at: now,
      is_mine: true,
      can_close: true,
    };
    topics.unshift(topic);
    replies[topic.topic_id] = [];
    response.send({ code: 201000, message: 'created', data: topic });
  },
  'POST /api/v1/collaboration/tasks/bbs/topics/:topicId/close': (
    request: { params: { topicId: string } },
    response: MockResponse,
  ) => {
    const topic = topics.find((item) => item.topic_id === request.params.topicId);
    if (!topic) return notFound(response);
    topic.status = 'CLOSED';
    topic.can_close = false;
    response.send(ok(null));
  },
};
