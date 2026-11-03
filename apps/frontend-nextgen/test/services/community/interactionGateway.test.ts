import { CommunityApiGateway } from '@/services/community/communityApiGateway';

jest.mock('@/services/backendApi/lab/botConfigController', () => ({
  listBrowseSubscriptions: jest.fn(),
}));
jest.mock('@/services/backendApi/community/interactionController', () => ({
  createCommunityInteraction: jest.fn(),
  getCommunityInteractionSummary: jest.fn(),
  getCommunityInteractionSummaryBatch: jest.fn(),
}));
jest.mock('@/services/backendApi/community/personalCenterController', () => ({
  getPersonalCenterActivities: jest.fn(),
}));

const {
  listBrowseSubscriptions,
} = jest.requireMock('@/services/backendApi/lab/botConfigController') as Record<string, jest.Mock>;

const {
  createCommunityInteraction,
  getCommunityInteractionSummary,
  getCommunityInteractionSummaryBatch,
} = jest.requireMock(
  '@/services/backendApi/community/interactionController',
) as Record<string, jest.Mock>;

const { getPersonalCenterActivities } = jest.requireMock(
  '@/services/backendApi/community/personalCenterController',
) as Record<string, jest.Mock>;

describe('CommunityApiGateway interaction and Personal Center contracts', () => {
  beforeEach(() => jest.clearAllMocks());

  it('maps interaction summaries and verifies that writes were persisted', async () => {
    createCommunityInteraction.mockResolvedValueOnce({ code: 201000, data: { recorded: true } });
    getCommunityInteractionSummary.mockResolvedValueOnce({
      code: 200000,
      data: {
        scope: 'ACTOR',
        actor_type: 'HUMAN',
        actor_id: '149844',
        stats: { follow_count: 2, topic_count: 3 },
      },
    });
    getCommunityInteractionSummaryBatch.mockResolvedValueOnce({
      code: 200000,
      data: {
        items: [
          {
            scope: 'TARGET',
            target_type: 'TOPIC',
            target_id: 'topic-1',
            stats: { view_count: 4, reply_participant_count: 5 },
          },
        ],
      },
    });

    const gateway = new CommunityApiGateway();
    const actor = { actorType: 'HUMAN' as const, id: '149844' };
    await expect(gateway.createInteraction({
      actor,
      actionType: 'FOLLOW',
      target: { targetType: 'HUMAN', id: '149844' },
      active: 1,
    })).resolves.toBeUndefined();
    await expect(gateway.getInteractionSummary({ actor })).resolves.toMatchObject({
      scope: 'ACTOR',
      actor,
      followCount: 2,
      topicCount: 3,
    });
    await expect(
      gateway.getInteractionSummaryBatch([{ target: { targetType: 'TOPIC', id: 'topic-1' } }]),
    ).resolves.toMatchObject([{ scope: 'TARGET', viewCount: 4, replyParticipantCount: 5 }]);
  });

  it('pages browse subscriptions to total and returns only subscribed bot ids', async () => {
    listBrowseSubscriptions
      .mockResolvedValueOnce({
        code: 200000,
        data: { total: 3, items: [{ bot_id: 'bot-A' }, { bot_id: 'bot-B' }] },
      })
      .mockResolvedValueOnce({
        code: 200000,
        data: { total: 3, items: [{ bot_id: 'bot-C' }] },
      });

    const botIds = await new CommunityApiGateway().listBrowseSubscriptions('149844');

    expect(listBrowseSubscriptions).toHaveBeenNthCalledWith(1, '149844', undefined, 1, 100);
    expect(listBrowseSubscriptions).toHaveBeenNthCalledWith(2, '149844', undefined, 2, 100);
    expect(botIds).toEqual(['bot-A', 'bot-B', 'bot-C']);
  });

  it('stops paging if a subscription page is unexpectedly empty', async () => {
    listBrowseSubscriptions.mockResolvedValueOnce({
      code: 200000,
      data: { total: 2, items: [] },
    });

    const botIds = await new CommunityApiGateway().listBrowseSubscriptions('149844');

    expect(listBrowseSubscriptions).toHaveBeenCalledTimes(1);
    expect(botIds).toEqual([]);
  });

  it('maps Personal Center activity rows and drops malformed rows', async () => {
    getPersonalCenterActivities.mockResolvedValueOnce({
      code: 200000,
      data: {
        total: 1,
        items: [
          {
            activity_type: 'LIKED',
            actor_type: 'BOT',
            actor_id: 'bot-A',
            target_type: 'POST',
            target_id: 'post-1',
            topic_id: 'topic-1',
            post_id: 'post-1',
            happened_at: '2026-10-27T10:00:00Z',
            body_preview: '回复内容',
          },
          { activity_type: 'LIKED', actor_id: '', target_id: 'missing' },
        ],
      },
    });

    const page = await new CommunityApiGateway().getPersonalCenterActivities({
      ownerUserId: '149844',
      actorScope: 'ALL',
      activityType: 'LIKED',
      targetType: 'ALL',
      page: 1,
      pageSize: 10,
    });

    expect(page.total).toBe(1);
    expect(page.items).toHaveLength(1);
    expect(page.items[0]).toMatchObject({
      activityType: 'LIKED',
      actor: { actorType: 'BOT', id: 'bot-A' },
      target: { targetType: 'POST', id: 'post-1' },
      postId: 'post-1',
    });
  });
});
