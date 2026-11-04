import taskMock from '../../mock/task';

type MockResponse = {
  sent?: unknown;
  statusCode?: number;
  send(value: unknown): MockResponse;
  status(code: number): MockResponse;
};

function makeResponse(): MockResponse {
  return {
    statusCode: undefined,
    sent: undefined,
    send(value: unknown) {
      this.sent = value;
      return this;
    },
    status(code: number) {
      this.statusCode = code;
      return this;
    },
  };
}

describe('task by_session mock', () => {
  it('maps the relay collaboration session to the test task', () => {
    const response = makeResponse();
    const sessionId = 'bcs_grp_2e7f631b4339490db9e552ed517d29be:d8c1340a';

    taskMock['GET /openapi/v1/collaboration/tasks/by_session'](
      { query: { session_id: sessionId } },
      response as never,
    );

    expect(response.statusCode).toBeUndefined();
    expect(response.sent).toEqual({
      code: 200000,
      message: 'success',
      data: {
        task_id: '08bf5459-daef-423e-a233-910c890aff12',
        owner_user_id: '35983',
        node_id: '08bf5459-daef-423e-a233-910c890aff12',
      },
    });
  });

  it('keeps unrelated sessions open as not found', () => {
    const response = makeResponse();

    taskMock['GET /openapi/v1/collaboration/tasks/by_session'](
      { query: { session_id: 'other-session' } },
      response as never,
    );

    expect(response.statusCode).toBe(404);
    expect(response.sent).toEqual({ code: 404000, message: 'task session not found' });
  });
});
