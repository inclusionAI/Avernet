import { getBotIamToken, getIamToken } from '@/services/backendApi/privateChat/iamTokenController';
import { afterEach, describe, expect, jest, test } from '@jest/globals';

describe('iamTokenController OpenAPI contract', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });
  test('uses OpenAPI and returns the enveloped token', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ code: 200000, message: 'OK', data: { iam_token: 'token-1' } }),
    } as Response);
    await expect(getIamToken('u1')).resolves.toBe('token-1');
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/org/user/iam-token?user_id=u1',
      expect.objectContaining({ method: 'GET' }),
    );
  });
  test('uses Bot-scoped IAM token endpoint for single chat', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ code: 200000, message: 'OK', data: { iam_token: 'bot-token' } }),
    } as Response);
    await expect(
      getBotIamToken('bot-1', 'u1', 'owner-1', 'online', { sessionKey: 'online-session' }),
    ).resolves.toBe('bot-token');
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot-1/iam-token?user_id=u1&entity_id=owner-1&stage=online&session_key=online-session',
      expect.objectContaining({ method: 'POST' }),
    );
  });
  test('uses the verified friend session parameters for an online friend Bot', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ code: 200000, message: 'OK', data: { iam_token: 'test-token' } }),
    } as Response);
    await expect(
      getBotIamToken('bot-1', 'u1', 'entity-1', 'online', {
        sessionKey: 'friend-session',
        friendUserId: 'u1',
        ownerId: 'owner-1',
      }),
    ).resolves.toBe('test-token');
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot-1/iam-token?user_id=u1&entity_id=entity-1&stage=online&owner_id=owner-1&f_user_id=u1&session_id=friend-session',
      expect.objectContaining({ method: 'POST' }),
    );
  });
  test('omits stage when no runtime override is needed', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ code: 200000, data: { iam_token: 'token' } }),
    } as Response);
    await getBotIamToken('personal', 'viewer', 'owner');
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/personal/iam-token?user_id=viewer&entity_id=owner',
      expect.objectContaining({ method: 'POST' }),
    );
  });
  test('carries device_uuid for the selected online Service Bot instance', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ code: 200000, message: 'OK', data: { iam_token: 'device-token' } }),
    } as Response);
    await expect(
      getBotIamToken('bot-1', 'u1', 'owner-1', 'online', {
        sessionKey: 'online-session',
        deviceUuid: 'device-a',
      }),
    ).resolves.toBe('device-token');
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot-1/iam-token?user_id=u1&entity_id=owner-1&stage=online&device_uuid=device-a&session_key=online-session',
      expect.objectContaining({ method: 'POST' }),
    );
  });
  test('omits device_uuid when the chat context has none', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ code: 200000, data: { iam_token: 'token' } }),
    } as Response);
    await getBotIamToken('personal', 'viewer', 'owner', 'online', { sessionKey: 's' });
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/personal/iam-token?user_id=viewer&entity_id=owner&stage=online&session_key=s',
      expect.objectContaining({ method: 'POST' }),
    );
  });
  // TODO(2026-11-19): 后端 iam_token 接口的 caller_runtime_target 参数暂未就绪,src 侧已注释
  // 暂不携带,以下三个「会携带」断言用例随之注释;接口上线后与 src/iamTokenController.ts
  // 同步取消注释恢复(openspec add-iam-caller-runtime-target)。
  // test('carries caller_runtime_target=service for a managed caller Bot chat', async () => {
  //   const fetch = jest.spyOn(globalThis, 'fetch').mockResolvedValue({
  //     ok: true,
  //     headers: new Headers({ 'content-type': 'application/json' }),
  //     json: async () => ({ code: 200000, message: 'OK', data: { iam_token: '<caller-result>' } }),
  //   } as Response);
  //   await expect(
  //     getBotIamToken('bot-1', 'u1', 'owner-1', 'online', {
  //       sessionKey: 'online-session',
  //       callerRuntimeTarget: 'service',
  //     }),
  //   ).resolves.toBe('<caller-result>');
  //   expect(fetch).toHaveBeenCalledWith(
  //     '/openapi/v1/bots/bot-1/iam-token?user_id=u1&entity_id=owner-1&stage=online&caller_runtime_target=service&session_key=online-session',
  //     expect.objectContaining({ method: 'POST' }),
  //   );
  // });
  // test('carries caller_runtime_target=caller_instance for a friend caller Bot chat', async () => {
  //   const fetch = jest.spyOn(globalThis, 'fetch').mockResolvedValue({
  //     ok: true,
  //     headers: new Headers({ 'content-type': 'application/json' }),
  //     json: async () => ({ code: 200000, message: 'OK', data: { iam_token: '<friend-result>' } }),
  //   } as Response);
  //   await expect(
  //     getBotIamToken('bot-1', 'u1', 'entity-1', 'online', {
  //       sessionKey: 'friend-session',
  //       friendUserId: 'u1',
  //       ownerId: 'owner-1',
  //       callerRuntimeTarget: 'caller_instance',
  //     }),
  //   ).resolves.toBe('<friend-result>');
  //   expect(fetch).toHaveBeenCalledWith(
  //     '/openapi/v1/bots/bot-1/iam-token?user_id=u1&entity_id=entity-1&stage=online&caller_runtime_target=caller_instance&owner_id=owner-1&f_user_id=u1&session_id=friend-session',
  //     expect.objectContaining({ method: 'POST' }),
  //   );
  // });
  // test('coexists with device_uuid for an online multi-instance caller Bot', async () => {
  //   const fetch = jest.spyOn(globalThis, 'fetch').mockResolvedValue({
  //     ok: true,
  //     headers: new Headers({ 'content-type': 'application/json' }),
  //     json: async () => ({ code: 200000, message: 'OK', data: { iam_token: '<pair-result>' } }),
  //   } as Response);
  //   await getBotIamToken('bot-1', 'u1', 'owner-1', 'online', {
  //     sessionKey: 'online-session',
  //     deviceUuid: 'device-a',
  //     callerRuntimeTarget: 'service',
  //   });
  //   expect(fetch).toHaveBeenCalledWith(
  //     '/openapi/v1/bots/bot-1/iam-token?user_id=u1&entity_id=owner-1&stage=online&device_uuid=device-a&caller_runtime_target=service&session_key=online-session',
  //     expect.objectContaining({ method: 'POST' }),
  //   );
  // });
  test('omits caller_runtime_target when the chat context has none', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ code: 200000, data: { iam_token: 'token' } }),
    } as Response);
    await getBotIamToken('personal', 'viewer', 'owner', 'draft', { sessionKey: 's' });
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/personal/iam-token?user_id=viewer&entity_id=owner&stage=draft&session_key=s',
      expect.objectContaining({ method: 'POST' }),
    );
  });
});
