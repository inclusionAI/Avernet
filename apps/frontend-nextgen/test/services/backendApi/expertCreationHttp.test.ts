import { createExpertBot, getExpertBotCreationStatus } from '@/services/expertMarketService';
import { useIdentityStore } from '@/stores/identityStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';

const originalFetch = globalThis.fetch;
const configuredUser = Object.getOwnPropertyDescriptor(globalThis, 'TEAMCLAW_OPENAPI_USER_ID');
const body = { bot_name: '我的产品经理', bot_desc: '分析需求', engine: 'openclaw', cluster_name: 'ACRA' as const, bot_type: 'personal' as const, space_id: '10' };

const reply = (data: unknown) => Promise.resolve({
  ok: true, headers: new Headers({ 'content-type': 'application/json' }),
  json: async () => ({ code: 200000, message: 'OK', data }),
} as Response);

afterEach(() => {
  globalThis.fetch = originalFetch;
  if (configuredUser) Object.defineProperty(globalThis, 'TEAMCLAW_OPENAPI_USER_ID', configuredUser);
  else Reflect.deleteProperty(globalThis, 'TEAMCLAW_OPENAPI_USER_ID');
  useIdentityStore.getState().reset();
  useWorkspaceStore.setState({ activeIdentityId: null });
});

const userCases: Array<[string, string | null]> = [['001', null], ['human_001', null], ['human:001', null], ['', 'human_001']];

test.each(userCases)('详情未初始化 identityStore 时，创建及轮询沿原身份合同规范化配置 %s / capability %s', async (configuredId, activeIdentityId) => {
  useIdentityStore.getState().reset();
  useWorkspaceStore.setState({ activeIdentityId });
  Object.defineProperty(globalThis, 'TEAMCLAW_OPENAPI_USER_ID', { configurable: true, value: configuredId });
  expect(useIdentityStore.getState().currentIdentityId).toBeUndefined();
  const fetchMock = jest.fn().mockImplementationOnce(() => reply({ bot_id: 'bot-1', iframe_url: '', redirect_url: '' }))
    .mockImplementationOnce(() => reply({ bot_id: 'bot-1', state: 'READY' }));
  globalThis.fetch = fetchMock;
  await expect(createExpertBot('product-manager', body)).resolves.toMatchObject({ bot_id: 'bot-1' });
  await expect(getExpertBotCreationStatus('bot-1')).resolves.toMatchObject({ state: 'READY' });
  expect(fetchMock).toHaveBeenNthCalledWith(1, '/openapi/v1/experts/product-manager/bots?user_id=001', expect.objectContaining({ method: 'POST', body: JSON.stringify(body), credentials: 'include' }));
  expect(fetchMock).toHaveBeenNthCalledWith(2, '/openapi/v1/bots/bot-1/with-manifest/status?user_id=001', expect.objectContaining({ method: 'GET', credentials: 'include' }));
});
