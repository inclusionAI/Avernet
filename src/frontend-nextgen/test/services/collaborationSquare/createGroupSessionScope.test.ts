import * as sessionController from '@/services/backendApi/collaboration/sessionController';
import { CollaborationSquareApiAdapter } from '@/services/collaborationSquare/collaborationSquareApiAdapter';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/collaboration/sessionController');
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const sc = sessionController as unknown as Record<string, jest.Mock<any>>;

beforeEach(() => {
  jest.clearAllMocks();
  sc.createSession.mockResolvedValue({
    code: 20100,
    message: '',
    request_id: 'r',
    data: { session_id: 's1', group_id: 'g1', participants: [], status: 'running', created_at: 1, updated_at: 1 },
  });
});

describe('collaborationSquareApiAdapter.createGroupSession 透传 message_view_scope', () => {
  it('options.messageViewScope 进入请求体', async () => {
    const adapter = new CollaborationSquareApiAdapter();
    await adapter.createGroupSession('g1', undefined, { title: 't', query: 'q', messageViewScope: 'participant' });
    const body = sc.createSession.mock.calls[0][1] as Record<string, unknown>;
    expect(body.message_view_scope).toBe('participant');
  });

  it('不传时请求体无该键', async () => {
    const adapter = new CollaborationSquareApiAdapter();
    await adapter.createGroupSession('g1', undefined, { title: 't' });
    const body = sc.createSession.mock.calls[0][1] as Record<string, unknown>;
    expect(Object.prototype.hasOwnProperty.call(body, 'message_view_scope')).toBe(false);
  });
});
