/** @jest-environment jsdom */
// CommunityPersonalCenter 覆盖率补齐:该文件独立驱动 index/ActivityList/FollowList/
// useFollowedAuthorTopics 的 100% 行覆盖(loading/error/空态/兜底上下文/回贴分组/
// 关注身份选择与主题列表加载各分支),不依赖同目录其它测试文件运行。
import type {
  CommunityTopic,
  InteractionSummary,
  PersonalCenterActivity,
  PersonalCenterTopicContext,
} from '@/domain/community/types';
import { CommunityPersonalCenter } from '@/components/Community/CommunityPersonalCenter';
import type { UsePersonalCenterResult } from '@/hooks/usePersonalCenter';
import { communityService } from '@/services/community';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
// 说明:jest.mock 工厂在 import 绑定初始化前执行,故工厂内必须使用注入的全局 jest,
// 这里从 @jest/globals 只导入断言/生命周期 API。
import { beforeEach, describe, expect, it } from '@jest/globals';

jest.mock('@/services/community', () => ({
  communityService: {
    listTopics: jest.fn(),
    getInteractionSummaryBatch: jest.fn(),
  },
}));

const baseTopicContext: PersonalCenterTopicContext = {
  title: 'PRD 风格主题卡片',
  bodyPreview: '这是一个带完整上下文的帖子内容预览。',
  topicType: 'discussion',
  status: 'open',
  author: { actorType: 'BOT', id: 'bot-A' },
  authorDisplayName: '协作助手',
  replyCount: 12,
  likeCount: 34,
  viewCount: 56,
  favoriteCount: 7,
  isHot: true,
  createdAt: '2026-10-27T09:00:00Z',
};

/** 构造一条个人中心活动;默认 AUTHORED 主帖,字段覆盖便于快速 fork 各形态。 */
function activity(overrides: Record<string, unknown> = {}): PersonalCenterActivity {
  return {
    activityType: 'AUTHORED',
    actor: { actorType: 'BOT', id: 'bot-A' },
    target: { targetType: 'TOPIC', id: 'topic-1' },
    topicId: 'topic-1',
    happenedAt: '2026-10-27T10:00:00Z',
    bodyPreview: 'PRD 风格主题卡片',
    topicContext: { ...baseTopicContext },
    ...overrides,
  } as unknown as PersonalCenterActivity;
}

/** 构造一条关注身份活动(HUMAN/BOT 自动落 followedContext)。 */
function identity(
  id: string,
  displayName: string,
  targetType: 'HUMAN' | 'BOT',
  overrides: Record<string, unknown> = {},
): PersonalCenterActivity {
  return activity({
    activityType: 'FOLLOWED',
    actor: { actorType: 'HUMAN', id: '149844' },
    target: { targetType, id },
    topicId: undefined,
    happenedAt: '2026-10-27T10:00:00Z',
    bodyPreview: displayName,
    topicContext: undefined,
    followedContext: { targetType, targetId: id, displayName },
    ...overrides,
  });
}

/** listTopics 返回的主题。 */
function topic(overrides: Partial<CommunityTopic> = {}): CommunityTopic {
  return {
    id: 'topic-9',
    title: '关注者的主题',
    body: '正文内容',
    status: 'open',
    topicType: 'discussion',
    author: { type: 'human', id: 'user-b', displayName: '关注者' },
    createdAt: '2026-10-27T09:00:00Z',
    isMine: false,
    canClose: false,
    canEdit: false,
    canDelete: false,
    ...overrides,
  };
}

/** 主题聚合计数(summary,target 为 TOPIC 时被 FollowList 采样)。 */
function summary(targetId: string, overrides: Partial<InteractionSummary> = {}): InteractionSummary {
  return {
    scope: 'TARGET',
    target: { targetType: 'TOPIC', id: targetId },
    viewCount: 321,
    likeCount: 123,
    favoriteCount: 54,
    followCount: 2,
    acceptCount: 3,
    topicCount: 1,
    replyCount: 9,
    acceptedReplyCount: 1,
    replyParticipantCount: 4,
    helpfulReplyCount: 1,
    ...overrides,
  };
}

/** 手动释放 Promise,用于竞态/加载中分支的控制。 */
function createDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const mockedCenter: UsePersonalCenterResult = {
  ownerUserId: '149844',
  identity: { userId: '149844', displayName: '国科', avatarUrl: 'https://example.test/guoke.png', online: true },
  activityType: 'AUTHORED',
  page: 1,
  pageSize: 10,
  activityPage: { items: [activity()], total: 1 },
  loading: false,
  error: null,
  setActivityType: jest.fn(),
  goToPage: jest.fn(),
  reload: jest.fn(),
  unfollow: jest.fn(),
};

jest.mock('@/hooks/usePersonalCenter', () => ({
  usePersonalCenter: () => mockedCenter,
  PERSONAL_CENTER_PAGE_SIZE: 10,
}));

function renderCenter(onOpenTopic = jest.fn()) {
  const onBack = jest.fn();
  const view = render(<CommunityPersonalCenter onBack={onBack} onOpenTopic={onOpenTopic} />);
  return { view, onOpenTopic, onBack };
}

describe('CommunityPersonalCenter 覆盖补齐', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedCenter.identity = {
      userId: '149844',
      displayName: '国科',
      avatarUrl: 'https://example.test/guoke.png',
      online: true,
    };
    mockedCenter.activityType = 'AUTHORED';
    mockedCenter.page = 1;
    mockedCenter.loading = false;
    mockedCenter.error = null;
    mockedCenter.activityPage = { items: [activity()], total: 1 };
    mockedCenter.unfollow = jest.fn().mockResolvedValue(undefined);
    jest.mocked(communityService.listTopics).mockReset();
    jest.mocked(communityService.getInteractionSummaryBatch).mockReset();
  });

  it('authority 缺失时头部名称回退到 ownerUserId', () => {
    mockedCenter.identity = null;
    renderCenter();

    // 无 identity 时 Avatar 名称与副标题都走 `?? center.ownerUserId` 分支
    // (Avatar 无 src 渲染首字母占位,aria-label 与副标题均为 ownerUserId)
    expect(screen.getByLabelText('149844')).toBeInTheDocument();
    expect(screen.getByText('149844')).toBeInTheDocument();
  });

  it('AUTHORED 列表:基础渲染与点击主题跳转', () => {
    const { onOpenTopic } = renderCenter();

    expect(screen.getByText('PRD 风格主题卡片')).toBeInTheDocument();
    expect(screen.getByText('协作助手')).toBeInTheDocument();
    fireEvent.click(screen.getByText('PRD 风格主题卡片'));
    expect(onOpenTopic).toHaveBeenCalledWith({ topicId: 'topic-1' });
  });

  it('loading=true 时两个 tab 均渲染骨架屏', () => {
    mockedCenter.loading = true;
    const first = renderCenter();
    expect(screen.getByLabelText('个人中心列表加载中')).toBeInTheDocument();
    first.view.unmount();

    mockedCenter.activityType = 'FOLLOWED';
    mockedCenter.activityPage = { items: [], total: 0 };
    renderCenter();
    expect(screen.getByLabelText('我的关注加载中')).toBeInTheDocument();
  });

  it('error 非空时两个 tab 均渲染失败态,重试触发 reload', () => {
    mockedCenter.error = '网络异常';
    const first = renderCenter();
    expect(screen.getByText('列表加载失败')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(mockedCenter.reload).toHaveBeenCalledTimes(1);
    first.view.unmount();

    mockedCenter.activityType = 'FOLLOWED';
    mockedCenter.activityPage = { items: [], total: 0 };
    renderCenter();
    expect(screen.getByText('关注列表加载失败')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(mockedCenter.reload).toHaveBeenCalledTimes(2);
  });

  it('列表为空时两个 tab 均渲染空态', () => {
    // AUTHORED 空态
    mockedCenter.activityPage = { items: [], total: 0 };
    const first = renderCenter();
    expect(screen.getByText('暂无内容')).toBeInTheDocument();
    first.view.unmount();

    // FOLLOWED 空态
    mockedCenter.activityType = 'FOLLOWED';
    renderCenter();
    expect(screen.getByText('暂无关注')).toBeInTheDocument();
  });

  it('缺 topicContext 时使用兜底上下文,无 topicId 的行不渲染', () => {
    mockedCenter.activityPage = {
      items: [
        // 兜底1:标题取 bodyPreview
        activity({ topicContext: undefined, bodyPreview: '压测结论' }),
        // 兜底2:bodyPreview 为空 → 兜底标题「社区主题」
        activity({ topicContext: undefined, bodyPreview: '', topicId: 'topic-f' }),
        // 无 topicId → personalCenterActivityToTopic 返回 null → 该行不渲染
        activity({ topicId: undefined, topicContext: undefined, bodyPreview: '无主题ID的历史活动' }),
        // LIKED + POST 且无上下文 → 走「回复:」兜底标题
        activity({
          activityType: 'LIKED',
          topicContext: undefined,
          bodyPreview: '赞过的回复',
          target: { targetType: 'POST', id: 'post-d' },
          postId: 'post-d',
        }),
      ],
      total: 4,
    };
    renderCenter();

    expect(screen.getByText('压测结论')).toBeInTheDocument();
    expect(screen.getByText('社区主题')).toBeInTheDocument();
    // 多条兜底行共享同一作者展示名
    expect(screen.getAllByText('Bot · bot-A').length).toBeGreaterThan(0);
    expect(screen.getByText('回复：赞过的回复')).toBeInTheDocument();
    // 无 topicId 的那条没有渲染行
    const list = screen.getByLabelText('个人中心内容列表');
    expect(list.children).toHaveLength(3);
  });

  it('LIKED 分别展示帖子与回复两条形态', () => {
    mockedCenter.activityType = 'LIKED';
    const sharedContext = { topicContext: { ...baseTopicContext, title: '主题标题', bodyPreview: '主题内容' } };
    mockedCenter.activityPage = {
      items: [
        activity({ activityType: 'LIKED', bodyPreview: '主题标题', ...sharedContext }),
        activity({
          activityType: 'LIKED',
          bodyPreview: '被点赞的回复内容',
          target: { targetType: 'POST', id: 'post-1' },
          postId: 'post-1',
          ...sharedContext,
        }),
      ],
      total: 2,
    };
    const { onOpenTopic } = renderCenter();

    expect(screen.getByText('帖子：主题标题')).toBeInTheDocument();
    expect(screen.getByText('回复：被点赞的回复内容')).toBeInTheDocument();
    fireEvent.click(screen.getByText('回复：被点赞的回复内容'));
    expect(onOpenTopic).toHaveBeenCalledWith({ topicId: 'topic-1', postId: 'post-1' });
  });

  it('REPLIED 按主题分组,直接回复与楼中楼都可跳转', () => {
    mockedCenter.activityType = 'REPLIED';
    const replyContext: PersonalCenterTopicContext = {
      ...baseTopicContext,
      title: '误报率怎么压下来',
      topicType: 'help',
      authorDisplayName: '风控分析 Bot',
    };
    mockedCenter.activityPage = {
      items: [
        activity({
          activityType: 'REPLIED',
          actor: { actorType: 'HUMAN', id: '149844' },
          target: { targetType: 'TOPIC', id: 'topic-2' },
          topicId: 'topic-2',
          topicContext: replyContext,
          happenedAt: '2026-10-27T10:00:00',
        }),
        activity({
          activityType: 'REPLIED',
          actor: { actorType: 'HUMAN', id: '149844' },
          target: { targetType: 'POST', id: 'post-parent' },
          topicId: 'topic-2',
          postId: 'post-child',
          bodyPreview: '我回复另一个回复的内容。',
          topicContext: replyContext,
          repliedToPost: {
            postId: 'post-parent',
            author: { actorType: 'BOT', id: 'bot-risk' },
            authorDisplayName: '风控分析 Bot',
            bodyPreview: '先按 true/false positive 分类标注误报。',
          },
          happenedAt: '2026-10-27T10:05:00Z',
        }),
      ],
      total: 2,
    };
    const { onOpenTopic } = renderCenter();

    // timezone-less UTC 串按北京时间展示
    expect(screen.getByText('最近回复：2026-10-27 18:00')).toBeInTheDocument();
    expect(screen.getByText('回复 @风控分析 Bot：先按 true/false positive 分类标注误报。')).toBeInTheDocument();

    fireEvent.click(screen.getByText('误报率怎么压下来'));
    expect(onOpenTopic).toHaveBeenLastCalledWith({ topicId: 'topic-2' });
    // 无 postId 的楼条目标题为分组标题,这里点楼中楼行
    const innerRow = screen.getByText('我回复另一个回复的内容。');
    fireEvent.click(innerRow);
    expect(onOpenTopic).toHaveBeenLastCalledWith({ topicId: 'topic-2', postId: 'post-child' });
  });

  it('REPLIED 兜底:未知时间展示「时间未知」,无 topicId 时标题兜底且点击不跳转', () => {
    mockedCenter.activityType = 'REPLIED';
    mockedCenter.activityPage = {
      items: [
        activity({
          activityType: 'REPLIED',
          topicId: 'topic-e',
          target: { targetType: 'TOPIC', id: 'topic-e' },
          bodyPreview: '时间缺失的回复',
          happenedAt: '   ',
          topicContext: { ...baseTopicContext, title: '时间空串分组' },
        }),
        activity({
          activityType: 'REPLIED',
          topicId: 'topic-bad',
          target: { targetType: 'TOPIC', id: 'topic-bad' },
          bodyPreview: '时间非法的回复',
          happenedAt: 'not-a-date',
          topicContext: { ...baseTopicContext, title: '时间非法分组' },
        }),
        // 无 topicId 且无上下文/正文(bodyPreview 为 nullish 才会走到兜底)→ 标题为「话题 #」
        activity({
          activityType: 'REPLIED',
          topicId: undefined,
          topicContext: undefined,
          bodyPreview: undefined,
          target: { targetType: 'POST', id: 'post-orphan' },
          happenedAt: '2026-10-27T10:00:00Z',
        }),
      ],
      total: 3,
    };
    const { onOpenTopic } = renderCenter();

    expect(screen.getAllByText('最近回复：时间未知')).toHaveLength(2);
    expect(screen.getByText('话题 #')).toBeInTheDocument();
    const callsBefore = onOpenTopic.mock.calls.length;
    fireEvent.click(screen.getByText('话题 #'));
    // 无 topicId → openTarget 为 null → 不触发跳转
    expect(onOpenTopic.mock.calls.length).toBe(callsBefore);
    expect(screen.getByText('时间缺失的回复')).toBeInTheDocument();
    expect(screen.getByText('时间非法的回复')).toBeInTheDocument();
  });

  it('FOLLOWED 列表:选中用户/键盘选中 Bot/查看主题/取消关注', async () => {
    mockedCenter.activityType = 'FOLLOWED';
    mockedCenter.activityPage = {
      items: [identity('user-b', '孙七', 'HUMAN', { avatarUrl: 'https://example.test/sunqi.png' }), identity('bot-z', '协作 Bot', 'BOT')],
      total: 2,
    };
    jest
      .mocked(communityService.listTopics)
      .mockImplementation((query) => {
        // 组件实际调用时始终携带 authorId;类型上落空时按空串兜底
        const authorId = query?.authorId ?? '';
        return Promise.resolve({ items: [topic({ id: 'topic-2', title: '孙七的调研报告', author: { type: 'human', id: authorId, displayName: '调研员' } })], total: 1 });
      });
    jest.mocked(communityService.getInteractionSummaryBatch).mockResolvedValue([summary('topic-2')]);
    const { onOpenTopic } = renderCenter();

    expect(screen.getByText('关注的用户')).toBeInTheDocument();
    expect(screen.getByText('关注的 Bot')).toBeInTheDocument();

    // 点击选中用户 → 拉取其主题
    fireEvent.click(screen.getByText('孙七'));
    await waitFor(() => expect(communityService.listTopics).toHaveBeenCalledWith({ authorId: 'user-b', limit: 20 }));
    await waitFor(() => expect(screen.getByText('孙七 的帖子')).toBeInTheDocument());
    expect(screen.getByText('孙七的调研报告')).toBeInTheDocument();
    expect(screen.getByLabelText('浏览 321')).toBeInTheDocument();

    fireEvent.click(screen.getByText('孙七的调研报告'));
    expect(onOpenTopic).toHaveBeenCalledWith({ topicId: 'topic-2' });

    // 非 Enter/空格按键不改变选中态
    fireEvent.keyDown(screen.getByText('孙七'), { key: 'Tab' });
    expect(screen.getByText('孙七 的帖子')).toBeInTheDocument();

    // 键盘 Enter 选中 Bot 身份
    fireEvent.keyDown(screen.getByText('协作 Bot'), { key: 'Enter' });
    await waitFor(() => expect(screen.getByText('协作 Bot 的帖子')).toBeInTheDocument());

    // 取消关注按钮触发宿主回调
    fireEvent.click(screen.getByRole('button', { name: '取消关注 孙七' }));
    expect(mockedCenter.unfollow).toHaveBeenCalledTimes(1);
  });

  it('FOLLOWED 列表:关注空分组与身份名称兜底链', () => {
    mockedCenter.activityType = 'FOLLOWED';
    // 仅用户 → Bot 分组为空
    mockedCenter.activityPage = { items: [identity('user-c', '李四', 'HUMAN')], total: 1 };
    const first = renderCenter();
    expect(screen.getByText('暂无关注的 Bot')).toBeInTheDocument();
    first.view.unmount();

    // 仅 Bot 且无 followedContext/bodyPreview → 显示 target.id(带冒号时拆 botUuid/botId)
    mockedCenter.activityPage = {
      items: [
        identity('bot-dd', '占位', 'BOT', {
          followedContext: undefined,
          bodyPreview: '',
        }),
      ],
      total: 1,
    };
    const second = renderCenter();
    expect(screen.getByText('暂无关注的用户')).toBeInTheDocument();
    expect(screen.getByText('bot-dd')).toBeInTheDocument();
    second.view.unmount();
  });

  it('FOLLOWED 主题列表:加载骨架 → 批量摘要失败/列表失败/空列表各有兜底', async () => {
    mockedCenter.activityType = 'FOLLOWED';
    mockedCenter.activityPage = {
      items: [
        identity('user-e', '艾克', 'HUMAN'),
        identity('user-f', '富兰', 'HUMAN'),
        identity('user-g', '古月', 'HUMAN'),
        identity('user-h', '胡桃', 'HUMAN'),
      ],
      total: 4,
    };
    const deferredList = createDeferred<{ items: ReturnType<typeof topic>[]; total: number }>();
    jest.mocked(communityService.listTopics).mockImplementation((query) => {
      const authorId = query?.authorId;
      if (authorId === 'user-e') return deferredList.promise;
      if (authorId === 'user-f') return Promise.resolve({ items: [topic({ id: 'topic-f', title: '富兰的专题' })], total: 1 });
      if (authorId === 'user-g') return Promise.reject(new Error('network down'));
      return Promise.resolve({ items: [], total: 0 });
    });
    // 摘要第二批里混入无 target 的条目,覆盖非 TOPIC 过滤分支
    jest.mocked(communityService.getInteractionSummaryBatch).mockImplementation((queries) => {
      const ids = queries.map((q) => q.target?.id);
      if (ids.includes('topic-f')) return Promise.reject(new Error('batch down'));
      return Promise.resolve([
        { ...summary('topic-e'), target: undefined },
        summary('topic-e'),
      ] as never);
    });
    renderCenter();

    // 艾克:请求挂起时展示主题骨架屏
    fireEvent.click(screen.getByText('艾克'));
    const postsSection = screen.getByLabelText('关注对象的帖子');
    await waitFor(() => expect(screen.getByText('艾克 的帖子')).toBeInTheDocument());
    expect(postsSection.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0);
    await act(async () => {
      deferredList.resolve({ items: [topic({ id: 'topic-e', title: '艾克的专题' })], total: 1 });
    });
    await waitFor(() => expect(screen.getByText('艾克的专题')).toBeInTheDocument());
    // 摘要一条无 target、一条 TOPIC → TOPIC 命中次数标签
    expect(screen.getByLabelText('浏览 321')).toBeInTheDocument();

    // 富兰:批量摘要失败 → 主题仍渲染,仅缺次数标签(空 summaries 兜底)
    fireEvent.click(screen.getByText('富兰'));
    await waitFor(() => expect(screen.getByText('富兰的专题')).toBeInTheDocument());

    // 古月:主题列表失败 → 空列表兜底
    fireEvent.click(screen.getByText('古月'));
    await waitFor(() => expect(screen.getByText('该关注对象暂无帖子')).toBeInTheDocument());

    // 胡桃:空列表直接返回,不发起摘要查询
    fireEvent.click(screen.getByText('胡桃'));
    await waitFor(() => expect(communityService.listTopics).toHaveBeenCalledWith({ authorId: 'user-h', limit: 20 }));
  });

  it('FOLLOWED 主题列表竞态:过期主题响应与过期摘要响应都被忽略', async () => {
    mockedCenter.activityType = 'FOLLOWED';
    mockedCenter.activityPage = {
      items: [
        identity('user-i', '阿斯', 'HUMAN'),
        identity('user-j', '贝塔', 'HUMAN'),
        identity('user-k', '凯歌', 'HUMAN'),
        identity('user-l', '李逵', 'HUMAN'),
        identity('user-n', '南山', 'HUMAN'),
        identity('user-o', '欧阳', 'HUMAN'),
        identity('user-p', '皮特', 'HUMAN'),
      ],
      total: 7,
    };
    const deferredList = createDeferred<{ items: ReturnType<typeof topic>[]; total: number }>();
    const deferredListReject = createDeferred<{ items: ReturnType<typeof topic>[]; total: number }>();
    const deferredBatch = createDeferred<ReturnType<typeof summary>[]>();
    const deferredBatchReject = createDeferred<ReturnType<typeof summary>[]>();
    jest.mocked(communityService.listTopics).mockImplementation((query) => {
      const authorId = query?.authorId;
      if (authorId === 'user-i') return deferredList.promise;
      if (authorId === 'user-p') return deferredListReject.promise;
      if (authorId === 'user-n') {
        return Promise.resolve({ items: [topic({ id: 'topic-n', title: '南山的专题' })], total: 1 });
      }
      if (authorId === 'user-k') return Promise.resolve({ items: [topic({ id: 'topic-k', title: '凯歌的调研' })], total: 1 });
      return Promise.resolve({ items: [], total: 0 });
    });
    jest.mocked(communityService.getInteractionSummaryBatch).mockImplementation((queries) => {
      const ids = queries.map((q) => q.target?.id);
      if (ids.includes('topic-k')) return deferredBatch.promise;
      if (ids.includes('topic-n')) return deferredBatchReject.promise;
      return Promise.resolve([]);
    });
    renderCenter();

    // 过期的主题响应被忽略
    fireEvent.click(screen.getByText('阿斯'));
    fireEvent.click(screen.getByText('贝塔'));
    await waitFor(() => expect(screen.getByText('该关注对象暂无帖子')).toBeInTheDocument());
    await act(async () => {
      deferredList.resolve({ items: [topic({ id: 'topic-i', title: '阿斯的过期主题' })], total: 1 });
    });
    expect(screen.queryByText('阿斯的过期主题')).not.toBeInTheDocument();

    // 过期的批量摘要失败响应同样被忽略(catch 内 requestId 守卫)
    fireEvent.click(screen.getByText('南山'));
    await waitFor(() => expect(screen.getByText('南山的专题')).toBeInTheDocument());
    fireEvent.click(screen.getByText('欧阳'));
    await waitFor(() => expect(communityService.listTopics).toHaveBeenCalledWith({ authorId: 'user-o', limit: 20 }));
    await act(async () => {
      deferredBatchReject.reject(new Error('stale batch failure'));
    });
    expect(screen.getByText('该关注对象暂无帖子')).toBeInTheDocument();

    // 过期的主题失败响应同样被忽略
    fireEvent.click(screen.getByText('皮特'));
    fireEvent.click(screen.getByText('贝塔'));
    await act(async () => {
      deferredListReject.reject(new Error('stale list failure'));
    });
    expect(screen.getByText('该关注对象暂无帖子')).toBeInTheDocument();

    // 过期的摘要响应被忽略(切到李逵后凯歌的迟到摘要不落地)
    fireEvent.click(screen.getByText('凯歌'));
    await waitFor(() => expect(screen.getByText('凯歌的调研')).toBeInTheDocument());
    fireEvent.click(screen.getByText('李逵'));
    await waitFor(() => expect(screen.getByText('该关注对象暂无帖子')).toBeInTheDocument());
    await act(async () => {
      deferredBatch.resolve([summary('topic-k')]);
    });
    // 老的摘要不把李逵的选中态/加载态改坏
    expect(screen.getByText('该关注对象暂无帖子')).toBeInTheDocument();
  });

  it('选中的关注身份从列表消失时清空选中态', async () => {
    mockedCenter.activityType = 'FOLLOWED';
    mockedCenter.activityPage = { items: [identity('user-m', '满仓', 'HUMAN')], total: 1 };
    jest.mocked(communityService.listTopics).mockResolvedValue({ items: [topic({ id: 'topic-m', title: '满仓的主题' })], total: 1 });
    jest.mocked(communityService.getInteractionSummaryBatch).mockResolvedValue([summary('topic-m')]);
    const { view } = renderCenter();

    fireEvent.click(screen.getByText('满仓'));
    await waitFor(() => expect(screen.getByText('满仓 的帖子')).toBeInTheDocument());

    // 列表变化后选中身份再也找不到 → effect 清空 selectedKey
    mockedCenter.activityPage = { items: [], total: 0 };
    view.rerender(<CommunityPersonalCenter onBack={jest.fn()} onOpenTopic={jest.fn()} />);
    expect(screen.queryByText('满仓 的帖子')).not.toBeInTheDocument();
    expect(screen.getByText('暂无关注')).toBeInTheDocument();
  });
});
