import { createFeedback } from '@/services/backendApi/feedback/feedbackController';
import * as http from '@/services/backendApi/httpClient';

jest.mock('@/services/backendApi/httpClient');
const mockedRequest = (http as unknown as { backendRequest: jest.Mock }).backendRequest;

describe('feedbackController', () => {
  beforeEach(() => mockedRequest.mockReset().mockResolvedValue({ code: 201000, data: { id: 1 } }));

  test('提交反馈走 /api/v1/feedback，body 含 reporter_id/module/content', async () => {
    await createFeedback({ reporterId: '900003', module: 'bbs', content: '建议' });
    expect(mockedRequest).toHaveBeenCalledWith('/api/v1/feedback', {
      method: 'POST',
      data: { reporter_id: '900003', module: 'bbs', content: '建议' },
      signal: undefined,
    });
  });
});
