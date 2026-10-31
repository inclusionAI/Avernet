import { CommunityService } from '../../../src/services/community/communityService';
import type { CommunityGateway } from '../../../src/services/community/communityGateway';

function gateway(overrides: Partial<CommunityGateway> = {}): CommunityGateway {
  return {
    listTopics: jest.fn(async () => ({ items: [], total: 0 })),
    getTopic: jest.fn(async () => ({
      id: 'topic-1',
      title: '主题',
      body: '正文',
      status: 'open' as const,
      topicType: 'discussion' as const,
      author: { type: 'human' as const, id: 'u1', displayName: '我' },
      replyCount: 0,
      latestActivityAt: '',
      createdAt: '',
      isMine: true,
      canClose: true,
      canEdit: true,
      canDelete: true,
    })),
    listReplies: jest.fn(async () => ({ items: [], total: 0 })),
    createReply: jest.fn(async () => 'created-reply'),
    createTopic: jest.fn(async (input) => ({
      id: 'created',
      title: input.title,
      body: input.body,
      status: 'open' as const,
      topicType: 'discussion' as const,
      author: { type: 'human' as const, id: input.authorId, displayName: input.authorName },
      replyCount: 0,
      latestActivityAt: '',
      createdAt: '',
      isMine: true,
      canClose: true,
      canEdit: true,
      canDelete: true,
    })),
    closeTopic: jest.fn(async () => undefined),
    updateTopic: jest.fn(async (input) => ({
      id: input.topicId,
      title: input.title,
      body: input.body,
      status: 'open' as const,
      topicType: 'discussion' as const,
      author: { type: 'human' as const, id: input.authorId, displayName: '我' },
      replyCount: 0,
      latestActivityAt: '',
      createdAt: '',
      isMine: true,
      canClose: true,
      canEdit: true,
      canDelete: true,
    })),
    deleteTopic: jest.fn(async () => undefined),
    setPostHelpful: jest.fn(async () => undefined),
    createInteraction: jest.fn(async () => undefined),
    getInteractionSummary: jest.fn(async () => ({
      scope: 'ACTOR' as const,
      viewCount: 0,
      likeCount: 0,
      favoriteCount: 0,
      followCount: 0,
      acceptCount: 0,
      topicCount: 0,
      replyCount: 0,
      acceptedReplyCount: 0,
      replyParticipantCount: 0,
      helpfulReplyCount: 0,
    })),
    getInteractionSummaryBatch: jest.fn(async () => []),
    getPersonalCenterActivities: jest.fn(async () => ({ items: [], total: 0 })),
    listBrowseSubscriptions: jest.fn(async () => []),
    ...overrides,
  };
}

function reply(id: string, createdAt: string) {
  return {
    id,
    topicId: 't',
    body: id === '1' ? '先' : '后',
    author: { type: 'human' as const, id: 'u', displayName: 'U' },
    createdAt,
    helpful: false,
  };
}

describe('CommunityService', () => {
  test('forwards search/scope pagination and preserves server-side activity order', async () => {
    const listTopics = jest.fn(async () => ({
      total: 2,
      items: [
        {
          id: 'new',
          title: '新',
          body: '',
          status: 'open' as const,
          topicType: 'discussion' as const,
          author: { type: 'bot' as const, id: 'b', displayName: 'B' },
          replyCount: 1,
          latestActivityAt: '2026-10-06T00:00:00Z',
          createdAt: '',
          isMine: true,
          canClose: false,
          canEdit: false,
          canDelete: false,
        },
        {
          id: 'old',
          title: '旧',
          body: '',
          status: 'open' as const,
          topicType: 'discussion' as const,
          author: { type: 'human' as const, id: 'u', displayName: 'U' },
          replyCount: 0,
          latestActivityAt: '2026-10-01T00:00:00Z',
          createdAt: '',
          isMine: true,
          canClose: false,
          canEdit: false,
          canDelete: false,
        },
      ],
    }));
    const service = new CommunityService(gateway({ listTopics }));

    const result = await service.listTopics({ search: ' Bot ', scope: 'mine', offset: 20, limit: 10 });

    expect(listTopics).toHaveBeenCalledWith({ search: 'Bot', scope: 'mine', offset: 20, limit: 10 }, undefined);
    expect(result.items.map((item) => item.id)).toEqual(['new', 'old']);
  });

  test('preserves backend helpful-first reply order (返回 {items,total}) and rejects blank publish input', async () => {
    const listReplies = jest.fn(async () => ({
      total: 2,
      items: [reply('2', '2026-10-06T12:00:00Z'), reply('1', '2026-10-06T10:00:00Z')],
    }));
    const createTopic = jest.fn();
    const service = new CommunityService(gateway({ listReplies, createTopic }));

    const result = await service.listReplies('t');
    expect(listReplies).toHaveBeenCalledWith('t', {}, undefined);
    expect(result.items.map((item) => item.id)).toEqual(['2', '1']);
    expect(result.total).toBe(2);

    await expect(service.createTopic({ title: ' ', body: '正文', authorId: 'u', authorName: '我' })).rejects.toThrow(
      '请输入主题标题',
    );
    expect(createTopic).not.toHaveBeenCalled();
  });

  test('scope=mine 透传 authorId 到网关（§2.4 author_id 真分页），本地不再按 isMine 过滤、total 来自网关', async () => {
    const listTopics = jest.fn(async () => ({
      total: 3,
      items: [
        {
          id: 'mine',
          title: '我的',
          body: '',
          status: 'open' as const,
          topicType: 'discussion' as const,
          author: { type: 'human' as const, id: 'me', displayName: 'Me' },
          replyCount: 0,
          latestActivityAt: '2026-10-06T00:00:00Z',
          createdAt: '',
          isMine: true,
          canClose: true,
          canEdit: true,
          canDelete: true,
        },
        {
          id: 'other',
          title: '别人的',
          body: '',
          status: 'open' as const,
          topicType: 'discussion' as const,
          author: { type: 'bot' as const, id: 'b', displayName: 'B' },
          replyCount: 0,
          latestActivityAt: '2026-10-05T00:00:00Z',
          createdAt: '',
          isMine: false,
          canClose: false,
          canEdit: false,
          canDelete: false,
        },
        {
          id: 'mine2',
          title: '我的2',
          body: '',
          status: 'open' as const,
          topicType: 'discussion' as const,
          author: { type: 'human' as const, id: 'me', displayName: 'Me' },
          replyCount: 0,
          latestActivityAt: '2026-10-04T00:00:00Z',
          createdAt: '',
          isMine: true,
          canClose: true,
          canEdit: true,
          canDelete: true,
        },
      ],
    }));
    const service = new CommunityService(gateway({ listTopics }));

    const result = await service.listTopics({ scope: 'mine', authorId: 'me' });

    expect(listTopics).toHaveBeenCalledWith({ scope: 'mine', authorId: 'me', search: undefined }, undefined);
    // 不再本地 isMine 过滤，也不再客户端重排；服务端已按最新活跃时间返回。
    expect(result.items.map((item) => item.id)).toEqual(['mine', 'other', 'mine2']);
    expect(result.total).toBe(3);
  });

  test('缺少 latestActivityAt 时保留服务端顺序', async () => {
    const listTopics = jest.fn(async () => ({
      total: 2,
      items: [
        {
          id: 'b',
          title: 'B',
          body: '',
          status: 'open' as const,
          topicType: 'discussion' as const,
          author: { type: 'human' as const, id: 'u', displayName: 'U' },
          replyCount: undefined,
          latestActivityAt: '2026-10-06T00:00:00Z',
          createdAt: '2026-10-05T00:00:00Z',
          isMine: false,
          canClose: false,
          canEdit: false,
          canDelete: false,
        },
        {
          id: 'a',
          title: 'A',
          body: '',
          status: 'open' as const,
          topicType: 'discussion' as const,
          author: { type: 'human' as const, id: 'u', displayName: 'U' },
          replyCount: undefined,
          latestActivityAt: undefined,
          createdAt: '2026-10-01T00:00:00Z',
          isMine: false,
          canClose: false,
          canEdit: false,
          canDelete: false,
        },
      ],
    }));
    const service = new CommunityService(gateway({ listTopics }));
    const result = await service.listTopics();
    expect(result.items.map((item) => item.id)).toEqual(['b', 'a']);
  });

  test('createReply trims vote body and forwards structured poll selection', async () => {
    const createReply = jest.fn(async () => 'created-reply');
    const service = new CommunityService(gateway({ createReply }));

    await expect(service.createReply({ topicId: 't', body: '  ', authorId: 'u' })).rejects.toThrow(
      '请输入回复内容',
    );
    expect(createReply).not.toHaveBeenCalled();

    await service.createReply({
      topicId: 't',
      body: '  投票选择：方案 A  ',
      authorId: 'u',
      pollSelection: [0],
    });
    expect(createReply).toHaveBeenCalledWith(
      {
        topicId: 't',
        body: '投票选择：方案 A',
        authorId: 'u',
        pollSelection: [0],
      },
      undefined,
    );
  });


  test('createReply 透传并裁剪楼中楼 replyToPostId', async () => {
    const createReply = jest.fn(async () => 'created-reply');
    const service = new CommunityService(gateway({ createReply }));

    await service.createReply({
      topicId: 't',
      body: '回复楼层',
      authorId: 'u',
      replyToPostId: ' reply-1 ',
    });

    expect(createReply).toHaveBeenCalledWith(
      {
        topicId: 't',
        body: '回复楼层',
        authorId: 'u',
        replyToPostId: 'reply-1',
      },
      undefined,
    );
  });

  test('allows title-and-body-only shares', async () => {
    const createTopic = jest.fn(async (input) => ({
      id: 'share-topic',
      title: input.title,
      body: input.body,
      status: 'open' as const,
      topicType: 'share' as const,
      author: { type: 'human' as const, id: input.authorId, displayName: input.authorName },
      replyCount: 0,
      latestActivityAt: '',
      createdAt: '',
      isMine: true,
      canClose: true,
      canEdit: true,
      canDelete: true,
    }));
    const service = new CommunityService(gateway({ createTopic }));

    await service.createTopic({
      title: ' 分享经验 ',
      body: ' 正文 ',
      topicType: 'share',
      authorId: 'u1',
      authorName: '我',
    });

    expect(createTopic).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '分享经验',
        body: '正文',
        topicType: 'share',
      }),
      undefined,
    );
  });

  test('enforces poll option limit and submits help topics without extra config', async () => {
    const createTopic = jest.fn(async (input) => ({
      id: 'topic-created',
      title: input.title,
      body: input.body,
      status: 'open' as const,
      topicType: input.topicType ?? ('discussion' as const),
      author: { type: 'human' as const, id: input.authorId, displayName: input.authorName },
      replyCount: 0,
      latestActivityAt: '',
      createdAt: '',
      isMine: true,
      canClose: true,
      canEdit: true,
      canDelete: true,
    }));
    const service = new CommunityService(gateway({ createTopic }));
    const common = { title: '标题', body: '正文', authorId: 'u1', authorName: '我' };

    await expect(
      service.createTopic({
        ...common,
        topicType: 'poll',
        poll: { selection: 'single', options: Array.from({ length: 11 }, (_, index) => `选项 ${index + 1}`) },
      }),
    ).rejects.toThrow('投票选项最多 10 个');

    await service.createTopic({ ...common, topicType: 'help' });
    const helpInput = createTopic.mock.calls[createTopic.mock.calls.length - 1][0];
    expect(helpInput).not.toHaveProperty('help');
  });

  test('updateTopic trims title/body and forwards the author to the gateway', async () => {
    const updateTopic = jest.fn(async () => ({
      id: 'topic-1',
      title: '新标题',
      body: '新正文',
      status: 'open' as const,
      topicType: 'discussion' as const,
      author: { type: 'human' as const, id: 'u1', displayName: '我' },
      replyCount: 0,
      latestActivityAt: '',
      createdAt: '',
      isMine: true,
      canClose: true,
      canEdit: true,
      canDelete: true,
    }));
    const service = new CommunityService(gateway({ updateTopic }));

    await expect(
      service.updateTopic({ topicId: 'topic-1', authorId: ' ', title: '  标题  ', body: '  正文  ' }),
    ).rejects.toThrow('缺少当前用户身份');
    expect(updateTopic).not.toHaveBeenCalled();

    await expect(
      service.updateTopic({ topicId: 'topic-1', authorId: 'u1', title: '  ', body: '正文' }),
    ).rejects.toThrow('请输入主题标题');
    expect(updateTopic).not.toHaveBeenCalled();

    await service.updateTopic({
      topicId: 'topic-1',
      authorId: 'u1',
      title: '  新标题  ',
      body: '  新正文  ',
    });

    expect(updateTopic).toHaveBeenLastCalledWith(
      {
        topicId: 'topic-1',
        authorId: 'u1',
        title: '新标题',
        body: '新正文',
      },
      undefined,
    );
  });

  test('deleteTopic 透传 Topic 与作者，空白作者被拒绝', async () => {
    const deleteTopic = jest.fn(async () => undefined);
    const service = new CommunityService(gateway({ deleteTopic }));

    await expect(service.deleteTopic('topic-1', ' ')).rejects.toThrow('缺少当前用户身份');
    expect(deleteTopic).not.toHaveBeenCalled();

    await service.deleteTopic('topic-1', 'u1');
    expect(deleteTopic).toHaveBeenCalledWith('topic-1', 'u1', undefined);
  });

  test('closeTopic 透传 author_id 到网关', async () => {
    const closeTopic = jest.fn(async () => undefined);
    const service = new CommunityService(gateway({ closeTopic }));
    await service.closeTopic('topic-1', 'u1');
    expect(closeTopic).toHaveBeenCalledWith('topic-1', 'u1', undefined);
  });

  test('setPostHelpful 透传 Topic、Post、作者类型和目标状态到网关', async () => {
    const setPostHelpful = jest.fn(async () => undefined);
    const service = new CommunityService(gateway({ setPostHelpful }));

    await service.setPostHelpful('topic-1', 'post-2', 'BOT', 'bot-1', true);

    expect(setPostHelpful).toHaveBeenCalledWith(
      'topic-1',
      'post-2',
      'BOT',
      'bot-1',
      true,
      undefined,
    );
  });

  test('getTopic 透传 viewer 身份参数且不改写网关结果', async () => {
    const topic = {
      id: 'topic-1',
      title: '主题',
      body: '正文',
      status: 'open' as const,
      topicType: 'poll' as const,
      author: { type: 'human' as const, id: 'u1', displayName: '我' },
      replyCount: 0,
      latestActivityAt: '',
      createdAt: '',
      isMine: true,
      canClose: true,
      canEdit: true,
      canDelete: true,
      viewer: { hasVoted: true, votePostId: 'post-vote', pollSelection: [0] },
    };
    const getTopic = jest.fn(async () => topic);
    const service = new CommunityService(gateway({ getTopic }));

    await expect(
      service.getTopic('topic-1', { viewerAuthorId: 'u1', viewerAuthorType: 'HUMAN' }),
    ).resolves.toEqual(topic);
    expect(getTopic).toHaveBeenCalledWith(
      'topic-1',
      { viewerAuthorId: 'u1', viewerAuthorType: 'HUMAN' },
      undefined,
    );
  });

  test('interaction methods validate dimensions and forward batch requests unchanged', async () => {
    const createInteraction = jest.fn(async () => undefined);
    const summaryResult = {
      scope: 'ACTOR' as const,
      viewCount: 0,
      likeCount: 0,
      favoriteCount: 0,
      followCount: 0,
      acceptCount: 0,
      topicCount: 0,
      replyCount: 0,
      acceptedReplyCount: 0,
      replyParticipantCount: 0,
      helpfulReplyCount: 0,
    };
    const getInteractionSummary = jest.fn(async () => summaryResult);
    const getInteractionSummaryBatch = jest.fn(async () => []);
    const service = new CommunityService(
      gateway({ createInteraction, getInteractionSummary, getInteractionSummaryBatch }),
    );
    const input = {
      actor: { actorType: 'HUMAN' as const, id: '149844' },
      actionType: 'LIKE' as const,
      target: { targetType: 'TOPIC' as const, id: 'topic-1' },
      active: 1 as const,
    };
    const query = { target: { targetType: 'TOPIC' as const, id: 'topic-1' } };

    await service.createInteraction(input);
    await service.getInteractionSummary(query);
    await service.getInteractionSummaryBatch([query]);

    expect(createInteraction).toHaveBeenCalledWith(input, undefined);
    expect(getInteractionSummary).toHaveBeenCalledWith(query, undefined);
    expect(getInteractionSummaryBatch).toHaveBeenCalledWith([query], undefined);

    await expect(
      service.createInteraction({
        actor: { actorType: 'HUMAN', id: ' ' },
        actionType: 'LIKE',
        target: query.target,
        active: 1 as const,
      }),
    ).rejects.toThrow('互动主体或目标不能为空');
    await expect(async () => service.getInteractionSummary({})).rejects.toThrow('缺少互动统计维度');
    await expect(async () => service.getInteractionSummaryBatch([])).rejects.toThrow('互动统计批次不能为空');
  });

  test('Personal Center forwards its owner-scoped query and pages subscription Bot source', async () => {
    const getPersonalCenterActivities = jest.fn(async () => ({ items: [], total: 0 }));
    const listBrowseSubscriptions = jest.fn(async () => ['bot-A']);
    const service = new CommunityService(
      gateway({ getPersonalCenterActivities, listBrowseSubscriptions }),
    );
    const query = {
      ownerUserId: '149844',
      actorScope: 'ALL' as const,
      activityType: 'AUTHORED' as const,
      targetType: 'ALL' as const,
      page: 1,
      pageSize: 10,
    };

    await service.getPersonalCenterActivities(query);
    await service.listBrowseSubscriptions('149844');

    expect(getPersonalCenterActivities).toHaveBeenCalledWith(query, undefined);
    expect(listBrowseSubscriptions).toHaveBeenCalledWith('149844', undefined);
  });
});
