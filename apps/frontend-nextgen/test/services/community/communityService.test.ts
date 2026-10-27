import type { CommunityGateway } from '../../../src/services/community/communityGateway';
import {
  MAX_COMMUNITY_ATTACHMENT_BYTES,
  CommunityService,
} from '../../../src/services/community/communityService';

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
    })),
    listReplies: jest.fn(async () => ({ items: [], total: 0 })),
    uploadAttachment: jest.fn(async (file) => ({
      id: 'attachment-1',
      filename: file.name,
      contentType: file.type || 'application/octet-stream',
      sizeBytes: file.size,
    })),
    downloadAttachment: jest.fn(async () => new Blob(['content'])),
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
    })),
    closeTopic: jest.fn(async () => undefined),
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
        },
      ],
    }));
    const service = new CommunityService(gateway({ listTopics }));

    const result = await service.listTopics({ search: ' Bot ', scope: 'mine', offset: 20, limit: 10 });

    expect(listTopics).toHaveBeenCalledWith({ search: 'Bot', scope: 'mine', offset: 20, limit: 10 }, undefined);
    expect(result.items.map((item) => item.id)).toEqual(['new', 'old']);
  });

  test('sorts replies chronologically (返回 {items,total}) and rejects blank publish input', async () => {
    const listReplies = jest.fn(async () => ({
      total: 2,
      items: [reply('2', '2026-10-06T12:00:00Z'), reply('1', '2026-10-06T10:00:00Z')],
    }));
    const createTopic = jest.fn();
    const service = new CommunityService(gateway({ listReplies, createTopic }));

    const result = await service.listReplies('t');
    expect(listReplies).toHaveBeenCalledWith('t', {}, undefined);
    expect(result.items.map((item) => item.id)).toEqual(['1', '2']);
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


  test('validates attachment size and forwards upload/download', async () => {
    const uploaded = {
      id: 'attachment-1',
      filename: 'guide.pdf',
      contentType: 'application/pdf',
      sizeBytes: 3,
    };
    const uploadAttachment = jest.fn(async () => uploaded);
    const downloaded = new Blob(['guide'], { type: 'application/pdf' });
    const downloadAttachment = jest.fn(async () => downloaded);
    const service = new CommunityService(gateway({ uploadAttachment, downloadAttachment }));

    await expect(service.uploadAttachment(new File([], 'empty.txt'), 'u1')).rejects.toThrow('不能上传空文件');
    const oversized = new File(['x'], 'huge.bin');
    Object.defineProperty(oversized, 'size', { value: MAX_COMMUNITY_ATTACHMENT_BYTES + 1 });
    await expect(service.uploadAttachment(oversized, 'u1')).rejects.toThrow('单个附件不能超过 20 MiB');
    expect(uploadAttachment).not.toHaveBeenCalled();

    const file = new File(['pdf'], 'guide.pdf', { type: 'application/pdf' });
    await expect(service.uploadAttachment(file, 'u1')).resolves.toEqual(uploaded);
    expect(uploadAttachment).toHaveBeenCalledWith(file, 'u1', undefined);

    await expect(service.downloadAttachment('attachment-1')).resolves.toBe(downloaded);
    expect(downloadAttachment).toHaveBeenCalledWith('attachment-1', undefined);
  });

  test('allows attachment-free shares and normalizes optional attachment ids', async () => {
    const createTopic = jest.fn(async (input) => ({
      id: 'share-topic',
      title: input.title,
      body: input.body,
      status: 'open' as const,
      topicType: 'share' as const,
      topicConfig: { share: { attachmentIds: input.share?.attachmentIds ?? [], attachments: [] } },
      author: { type: 'human' as const, id: input.authorId, displayName: input.authorName },
      replyCount: 0,
      latestActivityAt: '',
      createdAt: '',
      isMine: true,
      canClose: true,
    }));
    const service = new CommunityService(gateway({ createTopic }));
    const base = {
      title: ' 分享资料 ',
      body: ' 正文 ',
      topicType: 'share' as const,
      authorId: 'u1',
      authorName: '我',
    };

    await service.createTopic({ ...base, share: { attachmentIds: [] } });
    expect(createTopic).toHaveBeenLastCalledWith(
      expect.objectContaining({ share: { attachmentIds: [] } }),
      undefined,
    );
    await expect(
      service.createTopic({ ...base, share: { attachmentIds: ['attachment-1', ' attachment-1 '] } }),
    ).rejects.toThrow('分享附件不能重复');
    await expect(
      service.createTopic({
        ...base,
        share: { attachmentIds: ['a1', 'a2', 'a3', 'a4', 'a5', 'a6'] },
      }),
    ).rejects.toThrow('分享主题最多上传 5 个附件');

    await service.createTopic({ ...base, share: { attachmentIds: [' attachment-1 ', 'attachment-2'] } });
    expect(createTopic).toHaveBeenLastCalledWith(
      expect.objectContaining({
        title: '分享资料',
        body: '正文',
        share: { attachmentIds: ['attachment-1', 'attachment-2'] },
      }),
      undefined,
    );
  });

  test('enforces poll option limit and trims optional help expected time', async () => {
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

    await service.createTopic({
      ...common,
      topicType: 'help',
      help: { expectedTime: ' 希望本周内 ' },
    });
    expect(createTopic).toHaveBeenLastCalledWith(
      expect.objectContaining({ help: { expectedTime: '希望本周内' } }),
      undefined,
    );

    await expect(
      service.createTopic({
        ...common,
        topicType: 'help',
        help: { expectedTime: 'x'.repeat(129) },
      }),
    ).rejects.toThrow('期望解决时间不能超过 128 个字符');
  });

  test('closeTopic 透传 author_id 到网关', async () => {
    const closeTopic = jest.fn(async () => undefined);
    const service = new CommunityService(gateway({ closeTopic }));
    await service.closeTopic('topic-1', 'u1');
    expect(closeTopic).toHaveBeenCalledWith('topic-1', 'u1', undefined);
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
});
