import { getPersonalCenterActivities } from '@/services/backendApi/community/personalCenterController';
import * as http from '@/services/backendApi/httpClient';

jest.mock('@/services/backendApi/httpClient');
const mockedRequest = (http as unknown as { backendRequest: jest.Mock }).backendRequest;

describe('personalCenterController', () => {
  beforeEach(() => mockedRequest.mockReset().mockResolvedValue({ code: 200000, data: null }));

  it('reads the owner-scoped, server-paged activity projection', async () => {
    await getPersonalCenterActivities({
      ownerUserId: '149844',
      actorScope: 'ALL',
      activityType: 'LIKED',
      targetType: 'POST',
      page: 2,
      pageSize: 10,
    });

    expect(mockedRequest).toHaveBeenCalledWith(
      '/openapi/v1/collaboration/tasks/bbs/personal-center/activities',
      {
        method: 'GET',
        params: {
          owner_user_id: '149844',
          actor_scope: 'ALL',
          activity_type: 'LIKED',
          target_type: 'POST',
          page: 2,
          page_size: 10,
        },
        signal: undefined,
      },
    );
  });
});
