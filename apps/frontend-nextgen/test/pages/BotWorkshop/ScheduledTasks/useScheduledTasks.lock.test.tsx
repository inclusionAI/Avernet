/** @jest-environment jsdom */
// 个人 Bot 与老前端 Cron 页一致：不查编辑锁，可管理即可编辑；服务 Bot 保持锁门控。
import { useScheduledTasks } from '@/pages/BotWorkshop/ScheduledTasks/useScheduledTasks';
import '@testing-library/jest-dom';
import { act, render } from '@testing-library/react';

jest.mock('@/hooks/useBotWorkshopEditorIdentity', () => ({
  useBotWorkshopRequestIdentity: () => ({ ready: true, loading: false, userId: 'admin' }),
}));
jest.mock('@/services/botWorkshop/botEditorService', () => ({ botEditorService: { listRoutineModels: jest.fn() } }));
jest.mock('@/services/backendApi/httpClient', () => {
  const actual = jest.requireActual('@/services/backendApi/httpClient');
  return { ...actual, backendRequest: jest.fn() };
});

import { backendRequest } from '@/services/backendApi/httpClient';

let currentBotType = 'personal';
const request = backendRequest as jest.Mock;
const ok = (data: unknown, code = 200000) => ({ code, message: 'OK', data, request_id: 'trace' });
const botDetail = (botType: string) => ({
  bot_id: 'bot',
  bot_name: 'Bot',
  owner_id: 'admin',
  bot_type: botType,
  engine: 'openclaw',
});
const lockUrls = () => request.mock.calls.filter(([url]) => String(url).includes('edit-lock')).length;

beforeEach(() => {
  request.mockReset();
  request.mockImplementation(async (url: string) => {
    const path = String(url);
    if (path.endsWith('/caller-context')) return ok({ bot_call_type: 'owner' });
    if (path.endsWith('/edit-lock')) return ok({ locked: false, need_lock: false, holder_user_id: 'admin' });
    if (path.endsWith('/routines'))
      return ok({ total: 0, items: [], failed_targets: [] });
    return ok(botDetail(currentBotType));
  });
});

let latest: ReturnType<typeof useScheduledTasks>;
function Probe() {
  latest = useScheduledTasks('bot', 'admin', {}, 1, 20);
  return null;
}
function flush() {
  return act(async () => {
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });
  });
}

test('个人 Bot 不查询编辑锁，可管理即可编辑', async () => {
  render(<Probe />);
  await flush();
  await flush();
  expect(latest.context?.bot_type).toBe('personal');
  expect(latest.context?.can_manage).toBe(true);
  expect(latest.lock).toBeUndefined();
  expect(lockUrls()).toBe(0);
  expect(latest.editable).toBe(true);
});

test('服务 Bot 仍走编辑锁门控并按持锁结果判定可编辑', async () => {
  currentBotType = 'service';
  render(<Probe />);
  await flush();
  await flush();
  expect(latest.context?.bot_type).toBe('service');
  expect(lockUrls()).toBeGreaterThan(0);
  expect(latest.editable).toBe(true);
});
