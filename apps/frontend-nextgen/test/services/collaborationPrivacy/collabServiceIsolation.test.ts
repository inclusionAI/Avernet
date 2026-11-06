import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import { appExtension } from '@/extensions/collab';
import type { CollaborationBotDto } from '@/services/backendApi';
import * as backend from '@/services/backendApi';
import { createCollaborationPrivacyRuntimeAdapter } from '@/services/collaborationPrivacy/collaborationPrivacyRuntimeAdapter';

jest.mock('@/services/workspace/chatBridge', () => ({}));
jest.mock('@/services/bcs/UmdPanel', () => ({ registerUmdPanelHandler: jest.fn() }));

jest.mock('@/services/backendApi', () => ({
  listMyBots: jest.fn(),
  getCollaborationBot: jest.fn(),
  patchCollaborationBot: jest.fn(),
  listBots: jest.fn(),
  getOrgUser: jest.fn(),
  listOrgDepts: jest.fn(),
  getWorkerConfig: jest.fn(),
  updateWorkerConfig: jest.fn(),
  publishBotPublic: jest.fn(),
  grantTaskClaim: jest.fn(),
  revokeTaskClaim: jest.fn(),
}));

const api = jest.mocked(backend);
const dto: CollaborationBotDto = {
  kind: 'bot',
  bot_id: 'external-1',
  name: 'External Bot',
  visibility: 'protected',
  status: 'online',
  env: 'pre',
  created_at: 1,
  updated_at: 2,
  descriptor: { summary: '', domains: [], scopes: [], skills: [] },
  reachability: 'reachable',
  friend_check_in_strategy: 'DEPT_FREE',
  friend_ext: {
    view_scope_agent_friend_deps: ['示例集团 / 研发部'],
    no_check_scope_friend_deps: [{ deptNo: 'known', deptName: '示例集团 / 产品部' }, { deptNo: 'missing' }],
  },
};
const identity = { userId: '1234', displayName: '员工名称', online: true };

beforeEach(() => {
  jest.resetAllMocks();
  extendCapabilities({
    ...appExtension.capabilities,
    // collab-internal 保留内部展示策略，但 BCN-only 没有组织目录。
    getUserProfilePresentation: () => ({
      status: 'available',
      value: { preferAuthenticatedUserProfile: false, showDepartment: true },
    }),
    getHumanIdentity: () => ({ status: 'available', value: identity }),
  });
  api.listMyBots.mockResolvedValue({ code: 20000, data: { items: [dto], total: 1, offset: 0, limit: 20 } });
  api.getCollaborationBot.mockResolvedValue({ code: 20000, data: dto });
  api.patchCollaborationBot.mockImplementation(async (_id, body) => ({
    code: 20000,
    data: { ...dto, ...body, descriptor: dto.descriptor },
  }));
  api.getOrgUser.mockRejectedValue(new Error('organization service is not deployed'));
  api.listOrgDepts.mockRejectedValue(new Error('organization service is not deployed'));
  api.listBots.mockRejectedValue(new Error('engine service is not deployed'));
});

afterEach(() => extendCapabilities(defaultCapabilities));

it('production wiring loads and refreshes external configuration without org, engine, or worker requests', async () => {
  // Imported before extension registration: capability values must not be captured at module evaluation.
  const adapter = createCollaborationPrivacyRuntimeAdapter();
  const signal = new AbortController().signal;
  const overview = await adapter.loadOverview('1234', signal, {
    target: 'activeBot',
    botId: dto.bot_id,
    external: true,
  });
  expect(overview.currentUser).toMatchObject({ displayName: '员工名称', employeeNumber: '001234', departmentPath: [] });
  expect(overview.bots).toHaveLength(1);
  expect(overview.bots[0].publication.bot.organizationPaths).toEqual([['示例集团 / 研发部']]);
  expect(overview.bots[0].friendApproval.exemptOrganizationPaths).toEqual([['示例集团 / 产品部']]);
  await expect(adapter.refreshManagedBot(dto.bot_id, signal)).resolves.toMatchObject({ id: dto.bot_id });
  await expect(adapter.syncDepartment('1234', signal)).resolves.toMatchObject({
    displayName: '员工名称',
    departmentPath: [],
  });
  await expect(adapter.searchDepartments('研发', signal)).resolves.toEqual([]);
  for (const mode of ['none', 'all'] as const) {
    await expect(
      adapter.updateFriendApproval({ botId: dto.bot_id, config: { mode, exemptOrganizationPaths: [] } }, signal),
    ).resolves.toMatchObject({ mode, exemptOrganizationPaths: [] });
  }
  for (const scope of ['none', 'all'] as const) {
    await expect(
      adapter.submitPublication(
        { botId: dto.bot_id, audience: 'bot', isExternalBot: true, config: { scope, organizationPaths: [] } },
        signal,
      ),
    ).resolves.toMatchObject({ status: 'completed', config: { scope } });
  }
  expect(api.listMyBots).toHaveBeenCalledWith({ kind: 'bot' }, signal);
  expect(api.getCollaborationBot).toHaveBeenCalledWith(dto.bot_id, signal);
  expect(api.getOrgUser).not.toHaveBeenCalled();
  expect(api.listOrgDepts).not.toHaveBeenCalled();
  expect(api.listBots).not.toHaveBeenCalled();
  expect(api.getWorkerConfig).not.toHaveBeenCalled();
});

it('directory-disabled identity loads without bot requests when no authenticated profile is available', async () => {
  extendCapabilities({ getHumanIdentity: () => ({ status: 'available', value: null }) });
  const overview = await createCollaborationPrivacyRuntimeAdapter().loadOverview('1234', undefined, {
    target: 'currentUser',
  });
  expect(overview.currentUser).toMatchObject({ displayName: '001234', employeeNumber: '001234', departmentPath: [] });
  expect(overview.bots).toEqual([]);
  expect(api.getOrgUser).not.toHaveBeenCalled();
  expect(api.listMyBots).not.toHaveBeenCalled();
});

it('collab preserves an engine already supplied by BCN without catalog enrichment', async () => {
  api.listMyBots.mockResolvedValue({
    code: 20000,
    data: { items: [{ ...dto, engine: 'openclaw' }], total: 1, offset: 0, limit: 20 },
  });
  const overview = await createCollaborationPrivacyRuntimeAdapter().loadOverview('1234', undefined, {
    target: 'activeBot',
    botId: dto.bot_id,
    external: true,
  });
  expect(overview.bots[0].engine).toBe('OpenClaw');
  expect(api.listBots).not.toHaveBeenCalled();
});

it('full internal capabilities keep organization hydration and engine enrichment enabled', async () => {
  extendCapabilities({
    getOrganizationDirectoryEnabled: () => ({ status: 'available', value: true }),
    getCollaborationBotEngineLabelEnabled: () => ({ status: 'available', value: true }),
  });
  api.getOrgUser.mockResolvedValue({
    success: true,
    data: {
      user_id: '1234',
      username: 'staff',
      display_name: '目录名称',
      full_name: '目录名称',
      tenant: 'example',
      dept_no: 'known',
      dept_name: '产品部',
      dept_path: 'root/known',
    },
  });
  api.listOrgDepts.mockResolvedValue({
    success: true,
    data: [{ dept_no: 'missing', dept_name: '研发部', dept_path: 'root/missing' }],
  });
  api.listBots.mockResolvedValue({
    code: 200000,
    data: { total: 1, items: [{ bot_id: dto.bot_id, bot_name: 'External Bot', engine: 'openclaw', status: 'ACTIVE' }] },
  });
  const adapter = createCollaborationPrivacyRuntimeAdapter();
  const overview = await adapter.loadOverview('1234', undefined, {
    target: 'activeBot',
    botId: dto.bot_id,
    external: true,
  });
  expect(overview.currentUser.displayName).toBe('目录名称');
  expect(overview.bots[0].engine).toBe('OpenClaw');
  expect(api.getOrgUser).toHaveBeenCalled();
  expect(api.listOrgDepts).toHaveBeenCalledWith({ keyword: 'missing' }, undefined);
  expect(api.listBots).toHaveBeenCalledWith({ user_id: '1234', page: 1, page_size: 100 }, undefined);
});
