import {
  createCommunityInteraction,
  getCommunityInteractionSummary,
  getCommunityInteractionSummaryBatch,
} from '@/services/backendApi/community/interactionController';
import * as http from '@/services/backendApi/httpClient';

jest.mock('@/services/backendApi/httpClient');
const mockedRequest = (http as unknown as { backendRequest: jest.Mock }).backendRequest;

describe('interactionController', () => {
  beforeEach(() => mockedRequest.mockReset().mockResolvedValue({ code: 200000, data: null }));

  it('create interaction posts the actor/action/target state with active', async () => {
    await createCommunityInteraction({
      actor: { actorType: 'HUMAN', id: '149844' },
      actionType: 'LIKE',
      target: { targetType: 'TOPIC', id: 'topic-1' },
      active: 1,
    });

    expect(mockedRequest).toHaveBeenCalledWith(
      '/openapi/v1/collaboration/tasks/bbs/interactions',
      {
        method: 'POST',
        data: {
          actor_type: 'HUMAN',
          actor_id: '149844',
          action_type: 'LIKE',
          target_type: 'TOPIC',
          target_id: 'topic-1',
          active: 1,
        },
        signal: undefined,
      },
    );
  });

  it('single summary reads only the four summary dimensions without legacy viewer parameters', async () => {
    await getCommunityInteractionSummary({
      actor: { actorType: 'BOT', id: 'bot-1' },
      target: { targetType: 'POST', id: 'post-1' },
    });

    expect(mockedRequest).toHaveBeenCalledWith(
      '/openapi/v1/collaboration/tasks/bbs/interactions/summary',
      {
        method: 'GET',
        params: {
          actor_type: 'BOT',
          actor_id: 'bot-1',
          target_type: 'POST',
          target_id: 'post-1',
        },
        signal: undefined,
      },
    );
  });

  it('batch summary posts the ordered request items once', async () => {
    await getCommunityInteractionSummaryBatch([
      { actor: { actorType: 'HUMAN', id: '149844' } },
      { target: { targetType: 'TOPIC', id: 'topic-1' } },
    ]);

    expect(mockedRequest).toHaveBeenCalledTimes(1);
    expect(mockedRequest.mock.calls[0][1].data).toEqual({
      items: [
        { actor_type: 'HUMAN', actor_id: '149844' },
        { target_type: 'TOPIC', target_id: 'topic-1' },
      ],
    });
  });
});
