import {
  closeCommunityTopic,
  createCommunityReply,
  createCommunityTopic,
  getCommunityTopic,
  listCommunityReplies,
  listCommunityTopics,
} from '@/services/backendApi/community/communityController';
import * as http from '@/services/backendApi/httpClient';

// list/get/posts/create/close 均由 capability getBbsApiBase 注入前缀——mock 为当前公开 task 域路径。
jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getBbsApiBase: () => ({ status: 'available', value: '/openapi/v1/collaboration/tasks/bbs/topics' }),
  }),
}));
jest.mock('@/services/backendApi/httpClient');
const mockedRequest = (http as unknown as { backendRequest: jest.Mock }).backendRequest;

describe('communityController', () => {
  beforeEach(() => {
    mockedRequest.mockReset().mockResolvedValue({ code: 200000, data: null });
  });

  test('列表走 /openapi/v1/collaboration/tasks/bbs/topics，适配 keyword/page/page_size，scope 不下发（我的需 author_id，此处不测）', async () => {
    await listCommunityTopics({ search: ' Bot ', scope: 'mine', offset: 10, limit: 10 });
    expect(mockedRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/tasks/bbs/topics', {
      method: 'GET',
      params: { page: 2, page_size: 10, keyword: 'Bot' },
      signal: undefined,
    });
  });

  test('无搜索词时不下发 keyword', async () => {
    await listCommunityTopics({ offset: 0, limit: 20 });
    const args = mockedRequest.mock.calls[0][1];
    expect(args.params).toEqual({ page: 1, page_size: 20 });
    expect(args.params).not.toHaveProperty('keyword');
  });

  test('详情与回复路径走 /openapi/v1/collaboration/tasks/bbs/topics 并编码 topicId；回帖按 page/page_size 分页', async () => {
    await getCommunityTopic('topic/1');
    await listCommunityReplies('topic/1', { page: 2, pageSize: 15 });
    expect(mockedRequest).toHaveBeenNthCalledWith(1, '/openapi/v1/collaboration/tasks/bbs/topics/topic%2F1', {
      method: 'GET',
      params: {},
      signal: undefined,
    });
    expect(mockedRequest).toHaveBeenNthCalledWith(2, '/openapi/v1/collaboration/tasks/bbs/topics/topic%2F1/posts', {
      method: 'GET',
      params: { page: 2, page_size: 15 },
      signal: undefined,
    });
  });

  test('回帖默认下发 page=1/page_size=20', async () => {
    await listCommunityReplies('topic/1');
    expect(mockedRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/tasks/bbs/topics/topic%2F1/posts', {
      method: 'GET',
      params: { page: 1, page_size: 20 },
      signal: undefined,
    });
  });

  test('投票回复下发结构化 poll_selection，且前端不再携带楼层引用', async () => {
    await createCommunityReply({
      topicId: 'topic/1',
      body: '投票选择：方案 A',
      authorId: 'u1',
      authorName: '张三',
      pollSelection: [0],
    });

    const arg = mockedRequest.mock.calls[0];
    expect(arg?.[0]).toBe('/openapi/v1/collaboration/tasks/bbs/topics/topic%2F1/posts');
    expect(arg?.[1].data).toMatchObject({
      client_request_id: expect.any(String),
      author_type: 'HUMAN',
      author_id: 'u1',
      body: '投票选择：方案 A',
      author_display_name: '张三',
      poll_selection: { option_indices: [0] },
    });
    expect(arg?.[1].data).not.toHaveProperty('reply_to_post_id');
  });


  test('发布主题走 /openapi/v1/collaboration/tasks/bbs/topics，HUMAN 写体含 client_request_id/title/body/author_id + 作者快照 author_display_name/author_avatar_url；结帖 body 含 author_id', async () => {
    // §2.1 + §8 填充规则：HUMAN 发帖时把 displayName/avatarUrl 作为写入快照下发，供列表/详情/楼层回放。
    const input = {
      title: '标题',
      body: '正文',
      authorId: 'u1',
      authorName: '张三',
      authorAvatarUrl: 'https://avatar.example.com/photo/u1.140x140.jpg',
    };
    await createCommunityTopic(input);
    await closeCommunityTopic('topic/1', 'u1');
    expect(mockedRequest).toHaveBeenNthCalledWith(
      1,
      '/openapi/v1/collaboration/tasks/bbs/topics',
      expect.objectContaining({
        method: 'POST',
        data: expect.objectContaining({
          title: '标题',
          body: '正文',
          author_type: 'HUMAN',
          author_id: 'u1',
          client_request_id: expect.any(String),
          author_display_name: '张三',
          author_avatar_url: 'https://avatar.example.com/photo/u1.140x140.jpg',
        }),
        signal: undefined,
      }),
    );
    expect(mockedRequest).toHaveBeenNthCalledWith(2, '/openapi/v1/collaboration/tasks/bbs/topics/topic%2F1/close', {
      method: 'POST',
      data: { author_type: 'HUMAN', author_id: 'u1' },
      signal: undefined,
    });
  });

  test('创建投票主题下发 topic_type=POLL 和 topic_config.poll 配置', async () => {
    await createCommunityTopic({
      title: '投票主题',
      body: '请选择',
      authorId: 'u1',
      authorName: '张三',
      topicType: 'poll',
      poll: {
        selection: 'multi',
        options: ['方案 A', '方案 B'],
        deadlineAt: '2026-10-20T12:00:00Z',
      },
    });

    expect(mockedRequest.mock.calls[0][1].data).toMatchObject({
      topic_type: 'POLL',
      topic_config: {
        poll: {
          selection: 'MULTI',
          options: ['方案 A', '方案 B'],
          deadline_at: '2026-10-20T12:00:00Z',
        },
      },
    });
  });

  test('authorAvatarUrl 缺勤时不发 author_avatar_url 快照；author_display_name 仍下发（§2.1 不传则 null）', async () => {
    await createCommunityTopic({ title: '标题', body: '正文', authorId: 'u1', authorName: '张三' });
    const arg = mockedRequest.mock.calls[0];
    expect(arg?.[1].data).toHaveProperty('author_display_name', '张三');
    expect(arg?.[1].data).not.toHaveProperty('author_avatar_url');
  });

  test('authorName 仅空白时不发 author_display_name 快照（防发空字符串快照）', async () => {
    await createCommunityTopic({ title: '标题', body: '正文', authorId: 'u1', authorName: '   ' });
    const arg = mockedRequest.mock.calls[0];
    expect(arg?.[1].data).not.toHaveProperty('author_display_name');
  });

  test('详情请求透传 viewer 身份参数', async () => {
    await getCommunityTopic('topic/1', {
      viewerAuthorId: ' user-1 ',
      viewerAuthorType: 'HUMAN',
    });

    expect(mockedRequest).toHaveBeenCalledWith('/openapi/v1/collaboration/tasks/bbs/topics/topic%2F1', {
      method: 'GET',
      params: { viewer_author_id: 'user-1', viewer_author_type: 'HUMAN' },
      signal: undefined,
    });
  });
});
