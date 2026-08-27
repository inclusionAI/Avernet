import type { CollaborationPrivacyOverview, FriendApprovalConfig } from '@/domain/collaborationPrivacy/types';
import type { CollaborationBotDto, OrgDeptDto, OrgUserDto } from '@/services/backendApi';
import type { CollaborationPrivacyApiAdapter } from '@/services/collaborationPrivacy/collaborationPrivacyApiAdapter';
import type { FriendApprovalCommand } from '@/services/collaborationPrivacy/collaborationPrivacyGateway';
import {
  createCollaborationPrivacyRuntimeAdapter,
  type CollaborationPrivacyRuntimeDependencies,
} from '@/services/collaborationPrivacy/collaborationPrivacyRuntimeAdapter';
import { describe, expect, it, jest } from '@jest/globals';

function createBotDto(): CollaborationBotDto {
  return {
    kind: 'bot',
    bot_id: 'bot-real-1',
    name: '真实 Bot',
    visibility: 'protected',
    status: 'online',
    env: 'pre',
    descriptor: { summary: '真实', domains: [], scopes: [], skills: [] },
    reachability: 'reachable',
    created_at: 1,
    updated_at: 2,
  };
}

function createUserDto(): OrgUserDto {
  return {
    user_id: '447147',
    username: 'S090011826218',
    display_name: '真实用户',
    full_name: '真实用户',
    tenant: 'antgroup',
    dept_no: 'A4195',
    dept_name: '蚂蚁集团-大安全-协作平台',
    dept_path: '00001/36822/A4195',
  };
}

function createDependencies() {
  const listManagedBots = jest.fn(
    async (_params = {}, _signal?: AbortSignal): ReturnType<CollaborationPrivacyApiAdapter['listManagedBots']> => {
      void _params;
      void _signal;
      return { items: [createBotDto()], total: 1, offset: 0, limit: 20 };
    },
  );
  const patchManagedBot = jest.fn(
    async (): ReturnType<CollaborationPrivacyApiAdapter['patchManagedBot']> => createBotDto(),
  );
  const apiAdapter: CollaborationPrivacyApiAdapter = {
    listManagedBots,
    getManagedBot: async () => createBotDto(),
    patchManagedBot,
  };
  const setOverview = jest.fn((nextOverview: CollaborationPrivacyOverview) => {
    void nextOverview;
  });
  const updateFriendApproval = jest.fn(
    async (...args: [FriendApprovalCommand, AbortSignal?]): Promise<FriendApprovalConfig> => {
      return args[0].config;
    },
  );
  const friendApprovalAdapter = { setOverview, updateFriendApproval };
  const getOrgUser = jest.fn(async (_signal?: AbortSignal) => {
    void _signal;
    return { code: 200000, data: createUserDto() };
  });
  const listOrgDepts = jest.fn(async (_params: { keyword?: string }, _signal?: AbortSignal) => {
    void _params;
    void _signal;
    return {
      code: 200000,
      data: [
        {
          dept_no: 'A4195',
          dept_name: '蚂蚁集团-大安全-协作平台',
          dept_path: '00001/36822/A4195',
        } satisfies OrgDeptDto,
      ],
    };
  });
  const publishBotPublic = jest.fn(async (_botId: string, _body: unknown, _signal?: AbortSignal) => {
    void _botId;
    void _body;
    void _signal;
    return {
      code: 200000,
      data: {
        success: true,
        puid: 'puid-1',
        approval_url: '/admin/work-orders/puid-1',
        state: 'PROCESSING' as const,
        last_operate: null,
        error_msg: null,
      },
    };
  });
  const dependencies: CollaborationPrivacyRuntimeDependencies = {
    apiAdapter,
    getOrgUser,
    listOrgDepts,
    publishBotPublic,
    friendApprovalAdapter,
  };
  return {
    dependencies,
    apiAdapter,
    friendApprovalAdapter,
    listManagedBots,
    getOrgUser,
    listOrgDepts,
    publishBotPublic,
  };
}

describe('collaboration privacy runtime wiring', () => {
  it('loads the page overview from ready APIs and does not request the Mock overview route', async () => {
    const { dependencies, friendApprovalAdapter, listManagedBots, getOrgUser } = createDependencies();
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);

    await expect(adapter.loadOverview()).resolves.toMatchObject({
      currentUser: {
        displayName: '真实用户',
        employeeNumber: '447147',
        departmentPath: ['蚂蚁集团-大安全-协作平台'],
      },
      bots: [expect.objectContaining({ id: 'bot-real-1', name: '真实 Bot' })],
      organizationOptions: [],
    });

    expect(listManagedBots).toHaveBeenCalledWith({}, undefined);
    expect(getOrgUser).toHaveBeenCalledWith(undefined);
    expect(friendApprovalAdapter.setOverview).toHaveBeenCalledWith(expect.anything());
  });

  it('routes ready mutations and department search to real controllers', async () => {
    const { dependencies, apiAdapter, listOrgDepts, publishBotPublic } = createDependencies();
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    await adapter.loadOverview();

    const signal = new AbortController().signal;
    await adapter.syncDepartment(signal);
    await adapter.searchDepartments('协作', signal);
    await adapter.updateDirectSetting({ botId: 'bot-real-1', setting: 'profilePublic', value: true }, signal);
    await adapter.submitPublication(
      {
        botId: 'bot-real-1',
        audience: 'user',
        config: { scope: 'restricted', organizationPaths: [['蚂蚁集团', '大安全', '协作平台']] },
        deptEntries: [{ deptNo: 'A4195', deptName: '蚂蚁集团-大安全-协作平台' }],
      },
      signal,
    );

    expect(apiAdapter.patchManagedBot).toHaveBeenCalledWith('bot-real-1', { visibility: 'public' }, signal);
    expect(listOrgDepts).toHaveBeenCalledWith({ keyword: '协作' }, signal);
    expect(publishBotPublic).toHaveBeenCalledWith(
      'bot-real-1',
      {
        public_scope: 'user',
        visibility: 'public',
        view_depts: [{ deptNo: 'A4195', deptName: '蚂蚁集团-大安全-协作平台' }],
      },
      signal,
    );
  });

  it('does not expose a COMPLETED fast-path response as a pending approval', async () => {
    const { dependencies } = createDependencies();
    dependencies.publishBotPublic = jest.fn(async () => ({
      code: 200000,
      data: {
        success: true,
        puid: null,
        approval_url: null,
        state: 'COMPLETED' as const,
        last_operate: null,
        error_msg: null,
        visibility: 'private',
        visibility_field: 'visibility',
      },
    }));
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    await adapter.loadOverview();

    await expect(
      adapter.submitPublication({
        botId: 'bot-real-1',
        audience: 'user',
        config: { scope: 'none', organizationPaths: [] },
      }),
    ).resolves.toEqual({
      status: 'completed',
      config: { scope: 'none', organizationPaths: [] },
    });
  });

  it('keeps friend approval on the explicit Mock delegate', async () => {
    const { dependencies, friendApprovalAdapter } = createDependencies();
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    await adapter.loadOverview();
    const command: FriendApprovalCommand = {
      botId: 'bot-real-1',
      config: { mode: 'all', exemptOrganizationPaths: [] },
    };

    await adapter.updateFriendApproval(command);

    expect(friendApprovalAdapter.updateFriendApproval).toHaveBeenCalledWith(command, undefined);
    expect(dependencies.publishBotPublic).not.toHaveBeenCalled();
  });
});
