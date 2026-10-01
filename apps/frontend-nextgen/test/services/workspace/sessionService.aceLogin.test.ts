/** @jest-environment jsdom */
import { sessionService } from '@/services/workspace/sessionService';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

const originalFetch = global.fetch;
const aceBody = {
  actionType: 'LOGIN',
  buserviceErrorCode: 'USER_NOT_LOGIN',
  decisionBy: 'ACE',
  buserviceErrorMsg: 'https://login.example.com/pubLogin?goto=x',
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

describe('sessionService.loadBcsSessions ACE 旁路守护', () => {
  it('ACE body → 登记单飞跳转并返回空列表', async () => {
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(jsonOk(aceBody));

    const res = await sessionService.loadBcsSessions('g1', 0);

    expect(res).toEqual([]);
    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBe(aceBody.buserviceErrorMsg);
  });

  it('正常 200 信封不触发跳转、正常解析', async () => {
    global.fetch = jest.fn<typeof fetch>().mockResolvedValue(jsonOk({ code: 200000, data: { items: [], total: 0 } }));

    const res = await sessionService.loadBcsSessions('g1', 0);

    expect(res).toEqual([]);
    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBeUndefined();
  });
});
