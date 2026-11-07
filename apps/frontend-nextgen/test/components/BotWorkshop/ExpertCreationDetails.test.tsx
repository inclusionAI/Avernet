/** @jest-environment jsdom */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ExpertCreationDetails } from '@/components/BotWorkshop/ExpertCreationDetails';
import { backendRequest } from '@/services/backendApi/httpClient';
import { ExpertCreationResult } from '@/components/BotWorkshop/CreateBotModal/ExpertCreationResult';
import type { BotCreateInput } from '@/services/botWorkshop';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
const input: BotCreateInput = {
  scenario: 'cloud', name: '专家', description: '评审', engine: 'openclaw',
  spaceId: '10', ownership: 'personal', serviceMode: 'non-service', initialize: true,
};

test('失败结果保留原 Bot 和报告但不提供重放操作', () => {
  render(<ExpertCreationResult botId="same-bot" status={{
    bot_id: 'same-bot', iframe_url: '', redirect_url: '', state: 'APPLY_FAILED',
    apply: { apply_id: 'new-apply', result: 'PARTIAL', entries: [
      { category: 'skills', name: 'prd-review', action: 'failed', error: '凭证范围不允许' },
    ] },
  }} unknown={false} input={input} onClose={jest.fn()} />);
  expect(screen.getByText(/new-apply/)).toBeTruthy();
  expect(screen.getByText(/凭证范围不允许/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: '在原 Bot 重试配置' })).toBeNull();
  expect(screen.getByText(/same-bot/)).toBeTruthy();
});

jest.mock('@/services/backendApi/httpClient', () => ({
  ...jest.requireActual('@/services/backendApi/httpClient'), backendRequest: jest.fn(),
}));

test('Bot 管理可反复打开失败详情，所有请求只读原 Bot', async () => {
  const request = jest.mocked(backendRequest);
  request.mockReset();
  request.mockResolvedValue({ code: 200000, data: {
    bot_id: 'same-bot', iframe_url: '', redirect_url: '', state: 'APPLY_FAILED',
    bot: { bot_id: 'same-bot' },
    apply: { apply_id: 'failed-initial-apply', result: 'PARTIAL', entries: [
      { category: 'skills', name: 'prd-review', action: 'failed', error: '凭证范围不允许' },
    ] },
  } });
  render(<ExpertCreationDetails botId="same-bot" />);
  expect(request).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '专家创建详情' }));
  await screen.findByText(/凭证范围不允许/);
  expect(screen.queryByRole('button', { name: '在原 Bot 重试配置' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '留在当前页面' }));
  fireEvent.click(screen.getByRole('button', { name: '专家创建详情' }));
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  expect(request.mock.calls.every(([path, options]) =>
    path === '/openapi/v1/bots/same-bot/with-manifest/status' && options?.method === 'GET',
  )).toBe(true);
});
