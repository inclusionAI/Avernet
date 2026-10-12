import {
  closeCommunityTopic,
  createCommunityTopic,
  getCommunityTopic,
  listCommunityReplies,
  listCommunityTopics,
} from '@/services/backendApi/community/communityController';
import * as http from '@/services/backendApi/httpClient';

// list/get/posts/create/close 均由 capability getBbsApiBase 注入前缀——mock 为当前预发阶段的内面 /api/v1/bbs/topics（Unified 同 openapi 契约）。
jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getBbsApiBase: () => ({ status: 'available', value: '/api/v1/bbs/topics' }),
  }),
}));
jest.mock('@/services/backendApi/httpClient');
const mockedRequest = (http as unknown as { backendRequest: jest.Mock }).backendRequest;

describe('communityController', () => {
  beforeEach(() => {
    mockedRequest.mockReset().mockResolvedValue({ code: 200000, data: null });
  });

  test('列表走 /api/v1/bbs，适配 keyword/page/page_size，scope 不下发（我的需 author_id，此处不测）', async () => {
    await listCommunityTopics({ search: ' Bot ', scope: 'mine', offset: 10, limit: 10 });
    expect(mockedRequest).toHaveBeenCalledWith('/api/v1/bbs/topics', {
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

  test('详情与回复路径走 /api/v1/bbs 并编码 topicId；回帖按 page/page_size 分页', async () => {
    await getCommunityTopic('topic/1');
    await listCommunityReplies('topic/1', { page: 2, pageSize: 15 });
    expect(mockedRequest).toHaveBeenNthCalledWith(1, '/api/v1/bbs/topics/topic%2F1', {
      method: 'GET',
      signal: undefined,
    });
    expect(mockedRequest).toHaveBeenNthCalledWith(2, '/api/v1/bbs/topics/topic%2F1/posts', {
      method: 'GET',
      params: { page: 2, page_size: 15 },
      signal: undefined,
    });
  });

  test('回帖默认下发 page=1/page_size=20', async () => {
    await listCommunityReplies('topic/1');
    expect(mockedRequest).toHaveBeenCalledWith('/api/v1/bbs/topics/topic%2F1/posts', {
      method: 'GET',
      params: { page: 1, page_size: 20 },
      signal: undefined,
    });
  });

  test('发布主题走 /api/v1/bbs，HUMAN 写体含 client_request_id/title/body/author_id + 作者快照 author_display_name/author_avatar_url；结帖 body 含 author_id', async () => {
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
      '/api/v1/bbs/topics',
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
    expect(mockedRequest).toHaveBeenNthCalledWith(2, '/api/v1/bbs/topics/topic%2F1/close', {
      method: 'POST',
      data: { author_type: 'HUMAN', author_id: 'u1' },
      signal: undefined,
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
});
