/** @jest-environment jsdom */
import { loadBcsGroupDetail, loadBcsGroupSessions } from '@/services/workspace/groupExecuteService';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

const originalFetch = global.fetch;
const aceBody = {
  actionType: 'LOGIN',
  buserviceErrorCode: 'USER_NOT_LOGIN',
  decisionBy: 'ACE',
  buserviceErrorMsg: 'https://login.example.com/pubLogin?goto=y',
};

function jsonOk(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    headers: { get: () => 'application/json' },
    json: async () => body,
  } as unknown as Response;
}

beforeEach(() => {
  useLoginRedirectStore.getState().reset();
});
afterEach(() => {
  global.fetch = originalFetch;
});

describe('groupExecuteService ACE 旁路守护', () => {
  it('loadBcsGroupSessions ACE body → 登记跳转并返回空成功', async () => {
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(jsonOk(aceBody));

    const res = await loadBcsGroupSessions('g1');

    expect(res.ok).toBe(true);
    expect(res.ok && res.data).toEqual({ items: [], offset: 0, limit: 10, total: 0, hasMore: false });
    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBe(aceBody.buserviceErrorMsg);
  });

  it('loadBcsGroupDetail ACE body(群详情)→ 登记跳转并返回失败', async () => {
    // Promise.all 两个 fetch:群详情返回 ACE 触发守卫;会话 fetch 只需 ok。
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(jsonOk(aceBody));

    const res = await loadBcsGroupDetail('g1');

    expect(res.ok).toBe(false);
    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBe(aceBody.buserviceErrorMsg);
  });
});
