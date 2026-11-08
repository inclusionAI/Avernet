import { botEditorController } from '@/services/backendApi/bots/botEditorController';
import { getBotIdentity, getBotIdentityFile, updateBotIdentityFile } from '@/services/backendApi/bots/botIdentityController';
import { useIdentityStore } from '@/stores/identityStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';

beforeEach(() => {
  useIdentityStore.setState({ currentIdentityId: '447147' });
  useWorkspaceStore.setState({ activeIdentityId: 'human_447147' });
  jest.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true, headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({ code: 200000, data: { content: '# RULES', files: [] } }),
  } as Response);
});
afterEach(() => {
  jest.restoreAllMocks();
  useIdentityStore.getState().reset();
  useWorkspaceStore.setState({ activeIdentityId: null });
});

test.each(['draft', 'verify', 'online'] as const)('两个 Controller 的列表和单文件请求均保留调用人、真实 owner 与 %s', async (stage) => {
  await getBotIdentity('shared', '197262', stage);
  await getBotIdentityFile('shared', 'RULES', '197262', stage);
  await botEditorController.listIdentityFiles('shared', '197262', stage);
  await botEditorController.getIdentityFile('shared', 'RULES', '197262', stage);
  for (const [url, init] of jest.mocked(fetch).mock.calls) {
    const request = new URL(String(url), 'https://example.test');
    expect(request.searchParams.get('user_id')).toBe('447147');
    expect(request.searchParams.get('owner_id')).toBe('197262');
    expect(request.searchParams.get('stage')).toBe(stage);
    expect(init?.method).toBe('GET');
  }
  expect(fetch).toHaveBeenCalledTimes(4);
});

test('两个写入口均指向真实 owner 的草稿，不将登录协作者冒充 owner', async () => {
  await updateBotIdentityFile('shared', 'RULES', { content: '# updated' }, '197262');
  await botEditorController.updateIdentityFile('shared', 'RULES', '# updated', '197262');
  for (const [url, init] of jest.mocked(fetch).mock.calls) {
    const request = new URL(String(url), 'https://example.test');
    expect(request.pathname).toBe('/openapi/v1/bots/shared/identity/RULES');
    expect(Object.fromEntries(request.searchParams)).toEqual({ owner_id: '197262', stage: 'draft', user_id: '447147' });
    expect(init?.method).toBe('PUT');
    expect(JSON.parse(String(init?.body))).toEqual({ content: '# updated' });
  }
});

test('兼容 owner 本人调用时不指定 owner 和 stage 的旧签名', async () => {
  await getBotIdentityFile('mine', 'RULES');
  await botEditorController.getIdentityFile('mine', 'RULES');
  for (const [url] of jest.mocked(fetch).mock.calls) {
    const params = new URL(String(url), 'https://example.test').searchParams;
    expect(params.get('user_id')).toBe('447147');
    expect(params.has('owner_id')).toBe(false);
    expect(params.has('stage')).toBe(false);
  }
});
