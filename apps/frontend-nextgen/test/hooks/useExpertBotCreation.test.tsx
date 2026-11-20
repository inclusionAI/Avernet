/** @jest-environment jsdom */
import { act, renderHook, waitFor } from '@testing-library/react';
import { useExpertBotCreation } from '@/hooks/useExpertBotCreation';
import { reconcileOwnedBotGrants } from '@/services/collaboration/botGrantReconciliationService';
import { backendRequest } from '@/services/backendApi/httpClient';
import type { BotCreateInput } from '@/services/botWorkshop';
import type { AgentCodingTemplate } from '@/services/botWorkshop/agentCodingTemplateService';

jest.mock('@/services/collaboration/botGrantReconciliationService', () => ({
  reconcileOwnedBotGrants: jest.fn(),
}));
jest.mock('@/services/backendApi/httpClient', () => ({
  ...jest.requireActual('@/services/backendApi/httpClient'),
  backendRequest: jest.fn(),
}));
const request = jest.mocked(backendRequest);
const template: AgentCodingTemplate = {
  key: 'architect', versionId: 'v1', name: '架构 Bot', engine: 'claude_code',
  templateType: 'architect', source: 'official', fields: [], config: {},
  raw: { afterCreate: ['architect_name'] }, capabilityTags: [],
};
const input: BotCreateInput = {
  scenario: 'cloud', name: '专家架构 Bot', description: '设计架构', engine: 'aicoding',
  spaceId: '10', ownership: 'personal', serviceMode: 'non-service', initialize: true,
  agentCoding: { kind: 'template', template, values: { architect_name: '提交时的架构域' } },
};

beforeEach(() => { request.mockReset(); jest.mocked(reconcileOwnedBotGrants).mockClear(); });
afterEach(() => jest.useRealTimers());

test('专家任务返回可用 Bot 后沿原创建后动作保存提交的模板配置', async () => {
  request.mockImplementation(async (path) => {
    if (path.endsWith('/experts/architect/bots')) return { code: 200000, data: { bot_id: 'expert-1', iframe_url: '', redirect_url: '' } };
    if (path.endsWith('/with-manifest/status')) return { code: 200000, data: {
      bot_id: 'expert-1', state: 'READY', bot: { bot_id: 'expert-1', owner_id: '001', engine: 'claude_code' },
    } };
    return { code: 200000, data: {} };
  });
  const { result } = renderHook(() => useExpertBotCreation(true));
  await act(async () => result.current.submit('architect', input));
  await waitFor(() => expect(request).toHaveBeenCalledWith('/api/public/bots/expert-1/ext', expect.objectContaining({
    method: 'PATCH', data: { arch_domain: '提交时的架构域', is_domain_bot: true },
  })));
  expect(result.current.status?.state).toBe('READY');
  expect(reconcileOwnedBotGrants).toHaveBeenCalledWith('expert-1');
});

test('授权完成后只初始化原 Bot 一次，并且不再触发 DIMA / 审计类 after-create', async () => {
  jest.useFakeTimers();
  let polls = 0;
  request.mockImplementation(async (path) => {
    if (path.endsWith('/experts/architect/bots')) return { code: 200000, data: { bot_id: 'expert-2', iframe_url: 'https://example.test/auth', redirect_url: '' } };
    if (path.endsWith('/with-manifest/status')) return { code: 200000, data: ++polls === 1
      ? { bot_id: 'expert-2', state: 'AWAITING_AUTHORIZATION' }
      : { bot_id: 'expert-2', state: 'READY', bot: { bot_id: 'expert-2', owner_id: '001', engine: 'claude_code' } } };
    return { code: 200000, data: {} };
  });
  const { result } = renderHook(() => useExpertBotCreation(true));
  await act(async () => result.current.submit('architect', input));
  await act(async () => { jest.advanceTimersByTime(2000); });
  await waitFor(() => expect(result.current.initializing).toBe(false));
  const extCalls = request.mock.calls.filter(([path]) => path.endsWith('/ext'));
  expect(extCalls).toHaveLength(1);
  expect(request).toHaveBeenCalledWith('/api/public/bots/expert-2/ext', expect.objectContaining({
    method: 'PATCH', data: { arch_domain: '提交时的架构域', is_domain_bot: true },
  }));
  expect(request.mock.calls.some(([path]) => path.endsWith('/dima-workspace') || path === '/api/cron')).toBe(false);
});

test('模板初始化失败单独呈现，保留 Manifest 状态', async () => {
  request.mockImplementation(async (path) => {
    if (path.endsWith('/experts/architect/bots')) return { code: 200000, data: { bot_id: 'expert-3' } };
    if (path.endsWith('/with-manifest/status')) return { code: 200000, data: { bot_id: 'expert-3', state: 'APPLY_FAILED', message: '身份配置失败', bot: { bot_id: 'expert-3', owner_id: '001' } } };
    if (path.endsWith('/ext')) throw new Error('架构域保存失败');
    return { code: 200000, data: {} };
  });
  const { result } = renderHook(() => useExpertBotCreation(true));
  await act(async () => result.current.submit('architect', input));
  await waitFor(() => expect(result.current.afterCreateError).toBe('架构域保存失败'));
  expect(result.current.status?.state).toBe('APPLY_FAILED');
  expect(result.current.status?.message).toBe('身份配置失败');
  expect(result.current.initializing).toBe(false);
});

test('没有可用 Bot 的创建失败不触发模板初始化', async () => {
  request.mockImplementation(async (path) => path.endsWith('/experts/architect/bots')
    ? { code: 200000, data: { bot_id: 'expert-4' } }
    : { code: 200000, data: { bot_id: 'expert-4', state: 'CREATE_FAILED' } });
  const { result } = renderHook(() => useExpertBotCreation(true));
  await act(async () => result.current.submit('architect', input));
  await waitFor(() => expect(result.current.status?.state).toBe('CREATE_FAILED'));
  expect(request.mock.calls.some(([path]) => path.endsWith('/ext'))).toBe(false);
});

test('关闭重开时，旧模板初始化完成不会把新 Bot 头像写给旧 Bot', async () => {
  let completeExt!: (value: { code: number; data?: unknown }) => void;
  const ext = new Promise<{ code: number; data?: unknown }>((resolve) => { completeExt = resolve; });
  let created = 0;
  request.mockImplementation(async (path) => {
    if (path.endsWith('/experts/architect/bots')) return { code: 200000, data: { bot_id: `avatar-${++created}` } };
    if (path.endsWith('/with-manifest/status')) {
      const botId = path.includes('avatar-1') ? 'avatar-1' : 'avatar-2';
      return { code: 200000, data: { bot_id: botId, state: 'READY', bot: { bot_id: botId, owner_id: '001' } } };
    }
    if (path.endsWith('/ext')) return ext;
    return { code: 200000, data: {} };
  });
  const { result, rerender } = renderHook(({ open }) => useExpertBotCreation(open), { initialProps: { open: true } });
  await act(async () => result.current.submit('architect', {
    ...input, avatarUrl: 'avatar-first',
  }));
  await waitFor(() => expect(result.current.initializing).toBe(true));
  rerender({ open: false });
  rerender({ open: true });
  await act(async () => result.current.submit('architect', { ...input, engine: 'openclaw', agentCoding: undefined, avatarUrl: 'avatar-second' }));
  await act(async () => completeExt({ code: 200000, data: {} }));
  await waitFor(() => expect(request).toHaveBeenCalledWith('/openapi/v1/bots/avatar-1/avatar', { method: 'PUT', params: {}, data: { avatar_url: 'avatar-first' } }));
  expect(request).toHaveBeenCalledWith('/openapi/v1/bots/avatar-2/avatar', { method: 'PUT', params: {}, data: { avatar_url: 'avatar-second' } });
});

test('管理重新打开只读原创建失败，不用后续 Apply 改写历史', async () => {
  request.mockImplementation(async (path) => {
    if (path.endsWith('/with-manifest/status')) return { code: 200000, data: {
      bot_id: 'existing-1', state: 'APPLY_FAILED', bot: { bot_id: 'existing-1', owner_id: '001' },
      apply: { apply_id: 'initial-apply', result: 'PARTIAL', entries: [
        { category: 'skills', name: 'review', action: 'failed', error: '加载失败' },
      ] },
    } };
    throw new Error('unexpected request: ' + path);
  });
  const { result } = renderHook(() => useExpertBotCreation(true, 'existing-1'));
  await waitFor(() => expect(result.current.status?.state).toBe('APPLY_FAILED'));
  expect(result.current.status?.bot?.bot_id).toBe('existing-1');
  expect(result.current.status?.apply?.apply_id).toBe('initial-apply');
  expect(request.mock.calls).toHaveLength(1);
  expect(request).toHaveBeenCalledWith('/openapi/v1/bots/existing-1/with-manifest/status', {
    method: 'GET', params: {},
  });
  expect(reconcileOwnedBotGrants).not.toHaveBeenCalled();
});

test('详情查询失败只显示查询错误，不推断创建失败或重发写请求', async () => {
  jest.useFakeTimers();
  request.mockRejectedValueOnce(new Error('进度暂不可读取'));
  request.mockResolvedValue({ code: 200000, data: {
    bot_id: 'existing-2', state: 'APPLY_FAILED', bot: { bot_id: 'existing-2' },
  } });
  const { result } = renderHook(() => useExpertBotCreation(true, 'existing-2'));
  await act(async () => {});
  expect(result.current.error).toBe('进度暂不可读取');
  expect(result.current.status).toBeUndefined();
  await act(async () => { jest.advanceTimersByTime(2000); });
  expect(result.current.status?.bot?.bot_id).toBe('existing-2');
  expect(result.current.error).toBeUndefined();
  expect(request.mock.calls.every(([, options]) => options?.method === 'GET')).toBe(true);
});
