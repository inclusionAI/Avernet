/** @jest-environment jsdom */
import { TaskPanelFetcher } from '@/assets/TaskPanel/TaskPanelFetcher';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { afterEach, beforeEach, expect, it, jest } from '@jest/globals';
import { render, waitFor } from '@testing-library/react';

const originalFetch = global.fetch;
const aceBody = {
  actionType: 'LOGIN',
  buserviceErrorCode: 'USER_NOT_LOGIN',
  decisionBy: 'ACE',
  buserviceErrorMsg: 'https://login.example.com/pubLogin?goto=z',
};

beforeEach(() => {
  useLoginRedirectStore.getState().reset();
});
afterEach(() => {
  global.fetch = originalFetch;
  useLoginRedirectStore.getState().reset();
});

it('TaskPanelFetcher 收到 ACE body → 登记单飞跳转', async () => {
  global.fetch = jest.fn<typeof fetch>().mockResolvedValue({
    ok: true,
    status: 200,
    headers: { get: () => 'application/json' },
    json: async () => aceBody,
  } as unknown as Response);

  render(
    <TaskPanelFetcher apiBaseUrl="" taskId="t1">
      {() => null}
    </TaskPanelFetcher>,
  );

  await waitFor(() => {
    expect(useLoginRedirectStore.getState().pendingLoginUrl).toBe(aceBody.buserviceErrorMsg);
  });
});
