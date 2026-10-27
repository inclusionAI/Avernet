import { CommunityApiGateway } from '@/services/community/communityApiGateway';

jest.mock('@/services/backendApi/community/communityController', () => ({
  closeCommunityTopic: jest.fn(),
  createCommunityReply: jest.fn(),
  createCommunityTopic: jest.fn(),
  getCommunityTopic: jest.fn(),
  listCommunityReplies: jest.fn(),
  listCommunityTopics: jest.fn(),
  setCommunityPostHelpful: jest.fn(),
}));

const { setCommunityPostHelpful } = jest.requireMock(
  '@/services/backendApi/community/communityController',
) as Record<string, jest.Mock>;

describe('CommunityApiGateway', () => {
  beforeEach(() => jest.clearAllMocks());

  it('setPostHelpful 透传身份和目标状态并接受一致响应', async () => {
    setCommunityPostHelpful.mockResolvedValue({
      code: 200000,
      message: 'OK',
      data: { post_id: 'post-2', helpful: true },
    });

    await expect(
      new CommunityApiGateway().setPostHelpful(
        'topic-1',
        'post-2',
        'BOT',
        'bot-1',
        true,
      ),
    ).resolves.toBeUndefined();

    expect(setCommunityPostHelpful).toHaveBeenCalledWith(
      'topic-1',
      'post-2',
      'BOT',
      'bot-1',
      true,
      undefined,
    );
  });

  it.each([
    [{ post_id: 'post-other', helpful: true }],
    [{ post_id: 'post-2', helpful: false }],
  ])('setPostHelpful 拒绝与请求不一致的响应 %#', async (data) => {
    setCommunityPostHelpful.mockResolvedValue({ code: 200000, data });

    await expect(
      new CommunityApiGateway().setPostHelpful(
        'topic-1',
        'post-2',
        'HUMAN',
        '149844',
        true,
      ),
    ).rejects.toThrow('有帮助状态更新结果不一致');
  });

  it('setPostHelpful 传播失败信封消息', async () => {
    setCommunityPostHelpful.mockResolvedValue({
      code: 403000,
      message: 'only the topic author may mark helpful replies',
      data: null,
    });

    await expect(
      new CommunityApiGateway().setPostHelpful(
        'topic-1',
        'post-2',
        'HUMAN',
        'other-user',
        true,
      ),
    ).rejects.toThrow('only the topic author may mark helpful replies');
  });
});
