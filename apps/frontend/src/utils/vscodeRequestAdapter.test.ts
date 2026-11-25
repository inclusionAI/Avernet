test('mkdir preserves error status while login and other endpoints keep their response path', async () => {
  jest.resetModules();
  const postMessage = jest.fn();
  jest.doMock('./platform', () => ({ postMessageToVSCode: postMessage }));
  let listener: ((event: { data: unknown }) => void) | undefined;
  const oldWindow = global.window;
  (global as any).window = { addEventListener: (_type: string, handler: typeof listener) => { listener = handler; } };
  try {
    const { vscodeRequestAdapter } = await import('./vscodeRequestAdapter');
    async function request(url: string, status: number, data: unknown) {
      const promise = vscodeRequestAdapter({ url });
      const requestId = postMessage.mock.calls[postMessage.mock.calls.length - 1][0].requestId;
      listener?.({ data: { type: 'api-response', requestId, status, data } });
      return promise;
    }
    const error = await request('/api/resources/files/mkdir', 500, { success: true }).catch((e) => e);
    expect(error.response.status).toBe(500);
    const readOnly = await request('/api/resources/files/mkdir', 403, { detail: { code: 'filesystem_read_only' } }).catch((e) => e);
    expect(readOnly.response.status).toBe(403);
    expect(readOnly.response.data.detail.code).toBe('filesystem_read_only');
    expect((await request('/api/resources/files/mkdir', 401, { actionType: 'LOGIN' })).status).toBe(401);
    const other = await request('/api/other', 500, {});
    expect(other.status).toBe(500);
    expect(other.config).toEqual({});
    expect((await request('/api/resources/files/mkdir', 200, { success: true })).data.success).toBe(true);
  } finally {
    (global as any).window = oldWindow;
  }
});
