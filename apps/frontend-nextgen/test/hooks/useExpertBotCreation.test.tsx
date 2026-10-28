/** @jest-environment jsdom */
import { act, renderHook, waitFor } from '@testing-library/react';
import { useExpertBotCreation } from '@/hooks/useExpertBotCreation';
import { backendRequest } from '@/services/backendApi/httpClient';
import type { BotCreateInput } from '@/services/botWorkshop';
import type { AgentCodingTemplate } from '@/services/botWorkshop/agentCodingTemplateService';

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
const dimaTemplate: AgentCodingTemplate = { ...template, raw: {}, config: { capabilities: { dima_workspace: true } } };
const input: BotCreateInput = {
  scenario: 'cloud', name: '专家架构 Bot', description: '设计架构', engine: 'aicoding',
  spaceId: '10', ownership: 'personal', serviceMode: 'non-service', initialize: true,
  agentCoding: { kind: 'template', template, values: { architect_name: '提交时的架构域' } },
};

beforeEach(() => request.mockReset());
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
});

test('授权完成后只初始化原 Bot 一次，并保留提交时的 DIMA/托管选项', async () => {
  jest.useFakeTimers();
  let polls = 0;
  request.mockImplementation(async (path, options) => {
    if (path.endsWith('/experts/architect/bots')) return { code: 200000, data: { bot_id: 'expert-2', iframe_url: 'https://example.test/auth', redirect_url: '' } };
    if (path.endsWith('/with-manifest/status')) return { code: 200000, data: ++polls === 1
      ? { bot_id: 'expert-2', state: 'AWAITING_AUTHORIZATION' }
      : { bot_id: 'expert-2', state: 'READY', bot: { bot_id: 'expert-2', owner_id: '001', engine: 'claude_code' } } };
    if (path.endsWith('/dima-workspace')) return { code: 200000, data: { dima_space_id: 'review-space' } };
    if (path === '/openapi/v1/bots/expert-2') return { code: 200000, data: { status: 'ACTIVE' } };
    if (path === '/api/cron') return { code: 200000, data: options?.method === 'GET' ? [] : {} };
    return { code: 200000, data: {} };
  });
  const dimaInput: BotCreateInput = { ...input, agentCoding: {
    kind: 'template', template: dimaTemplate,
    values: { is_hosted_24x7: true },
  } };
  const { result, rerender } = renderHook(() => useExpertBotCreation(true));
  await act(async () => result.current.submit('architect', dimaInput));
  expect(request.mock.calls.filter(([path]) => path.endsWith('/dima-workspace'))).toHaveLength(0);
  await act(async () => { jest.advanceTimersByTime(2000); });
  expect(request).toHaveBeenCalledWith('/api/aicoding/bot/expert-2/dima-workspace', expect.objectContaining({ params: { user_id: '001' } }));
  expect(request).toHaveBeenCalledWith('/api/cron', expect.objectContaining({ method: 'POST', data: expect.objectContaining({ bot_id: 'expert-2', owner_id: '001' }) }));
  rerender();
  await act(async () => { jest.advanceTimersByTime(10000); });
  expect(request.mock.calls.filter(([path]) => path.endsWith('/dima-workspace'))).toHaveLength(1);
  expect(request.mock.calls.filter(([path]) => path.endsWith('/experts/architect/bots'))).toHaveLength(1);
  jest.useRealTimers();
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
  expect(request.mock.calls.some(([path]) => path.endsWith('/ext') || path.endsWith('/dima-workspace'))).toBe(false);
});

test('关闭重开时，旧模板初始化完成不会把新 Bot 头像写给旧 Bot', async () => {
  let completeWorkspace!: (value: { code: number; data: { dima_space_id: string } }) => void;
  const workspace = new Promise<{ code: number; data: { dima_space_id: string } }>((resolve) => { completeWorkspace = resolve; });
  let created = 0;
  request.mockImplementation(async (path, options) => {
    if (path.endsWith('/experts/architect/bots')) return { code: 200000, data: { bot_id: `avatar-${++created}` } };
    if (path.endsWith('/with-manifest/status')) {
      const botId = path.includes('avatar-1') ? 'avatar-1' : 'avatar-2';
      return { code: 200000, data: { bot_id: botId, state: 'READY', bot: { bot_id: botId, owner_id: '001' } } };
    }
    if (path.endsWith('/last-apply')) return { code: 200000, data: { apply_id: '', entries: [] } };
    if (path.endsWith('/dima-workspace')) return workspace;
    return { code: 200000, data: options?.data };
  });
  const { result, rerender } = renderHook(({ open }) => useExpertBotCreation(open), { initialProps: { open: true } });
  await act(async () => result.current.submit('architect', {
    ...input, avatarUrl: 'avatar-first', agentCoding: {
      kind: 'template', template: dimaTemplate, values: {},
    },
  }));
  await waitFor(() => expect(result.current.initializing).toBe(true));
  rerender({ open: false });
  rerender({ open: true });
  await act(async () => result.current.submit('architect', { ...input, engine: 'openclaw', agentCoding: undefined, avatarUrl: 'avatar-second' }));
  await act(async () => completeWorkspace({ code: 200000, data: { dima_space_id: 'workspace-1' } }));
  await waitFor(() => expect(request).toHaveBeenCalledWith('/openapi/v1/bots/avatar-1/avatar', { method: 'PUT', params: {}, data: { avatar_url: 'avatar-first' } }));
  expect(request).toHaveBeenCalledWith('/openapi/v1/bots/avatar-2/avatar', { method: 'PUT', params: {}, data: { avatar_url: 'avatar-second' } });
});

test('原 Bot 配置失败可恢复并显示新报告，不再受旧创建终态影响', async () => {
  jest.useFakeTimers();
  let recoveryPolls = 0;
  request.mockImplementation(async (path) => {
    if (path.endsWith('/experts/architect/bots')) return { code: 200000, data: { bot_id: 'recover-1' } };
    if (path.endsWith('/with-manifest/status')) return { code: 200000, data: {
      bot_id: 'recover-1', state: 'APPLY_FAILED', bot: { bot_id: 'recover-1', owner_id: '001' },
      apply: { apply_id: 'initial-apply', result: 'FAILED', entries: [] },
    } };
    if (path.endsWith('/config-manifest/apply')) return { code: 200000, data: { apply_id: 'recovery-apply', result: 'RUNNING' } };
    if (path.endsWith('/applies/recovery-apply')) return { code: 200000, data: {
      apply_id: 'recovery-apply', result: ++recoveryPolls === 1 ? 'RUNNING' : 'PARTIAL',
      entries: [{ category: 'skills', name: 'review', action: 'failed', error: '加载失败' }],
    } };
    return { code: 200000, data: {} };
  });
  const { result } = renderHook(() => useExpertBotCreation(true));
  await act(async () => result.current.submit('architect', { ...input, engine: 'openclaw', agentCoding: undefined }));
  expect(result.current.status?.state).toBe('APPLY_FAILED');
  await act(async () => result.current.recover());
  expect(result.current.status?.state).toBe('APPLYING');
  await act(async () => { jest.advanceTimersByTime(2000); });
  expect(result.current.status?.state).toBe('APPLY_FAILED');
  expect(result.current.status?.apply?.apply_id).toBe('recovery-apply');
  expect(request.mock.calls.filter(([path]) => path.endsWith('/experts/architect/bots'))).toHaveLength(1);
  expect(request).toHaveBeenCalledWith('/openapi/v1/bots/recover-1/config-manifest/apply', expect.objectContaining({
    method: 'POST', params: expect.objectContaining({ recover_creation: true }),
  }));
});

test('管理重新打开后读取已完成的新 Apply，不把旧创建失败当当前结果', async () => {
  request.mockImplementation(async (path) => {
    if (path.endsWith('/with-manifest/status')) return { code: 200000, data: {
      bot_id: 'existing-1', state: 'APPLY_FAILED', bot: { bot_id: 'existing-1', owner_id: '001' },
    } };
    if (path.endsWith('/last-apply')) return { code: 200000, data: {
      apply_id: 'recovered-before-open', trigger: 'explicit', result: 'SUCCEEDED', entries: [],
    } };
    return { code: 200000, data: {} };
  });
  const { result } = renderHook(() => useExpertBotCreation(true, 'existing-1'));
  await waitFor(() => expect(result.current.status?.state).toBe('READY'));
  expect(result.current.status?.apply?.apply_id).toBe('recovered-before-open');
  expect(request.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);
});

test('后续普通 Apply 失败不冒充原专家初始化恢复', async () => {
  request.mockImplementation(async (path) => path.endsWith('/with-manifest/status')
    ? { code: 200000, data: { bot_id: 'edited-1', state: 'READY', bot: { bot_id: 'edited-1' } } }
    : { code: 200000, data: { apply_id: 'user-apply', trigger: 'explicit', result: 'FAILED', state: { kind: 'manifest', reference: null }, entries: [] } });
  const { result } = renderHook(() => useExpertBotCreation(true, 'edited-1'));
  await waitFor(() => expect(result.current.status?.state).toBe('APPLY_FAILED'));
  expect(result.current.status?.recoverable).toBe(false);
  await act(async () => result.current.recover());
  expect(request.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);
});
