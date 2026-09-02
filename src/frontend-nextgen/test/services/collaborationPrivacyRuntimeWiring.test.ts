import type { FriendApprovalConfig } from '@/domain/collaborationPrivacy/types';
import type { CollaborationBotDto, OrgDeptDto, OrgUserDto } from '@/services/backendApi';
import { grantTaskClaim, revokeTaskClaim } from '@/services/backendApi';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import type { CollaborationPrivacyApiAdapter } from '@/services/collaborationPrivacy/collaborationPrivacyApiAdapter';
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
    engine: 'openclaw',
    visibility: 'protected',
    status: 'online',
    env: 'pre',
    descriptor: { summary: '真实', domains: [], scopes: [], skills: [] },
    reachability: 'reachable',
    friend_ext: {
      public_user_approval: { puid: 'p-user', status: 'AGREE' },
      view_scope_agent_friend_deps: ['A1000'],
      no_check_scope_friend_deps: [],
    },
    friend_check_in_strategy: 'APPROVAL',
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
    async (
      botId: string,
      body: Parameters<CollaborationPrivacyApiAdapter['patchManagedBot']>[1],
      _signal?: AbortSignal,
    ): ReturnType<CollaborationPrivacyApiAdapter['patchManagedBot']> => {
      void _signal;
      const currentBot = createBotDto();
      const descriptor = body.descriptor
        ? { ...currentBot.descriptor!, ...structuredClone(body.descriptor) }
        : currentBot.descriptor;
      return { ...currentBot, bot_id: botId, ...structuredClone(body), descriptor };
    },
  );
  const getManagedBot = jest.fn(
    async (botId: string, _signal?: AbortSignal): ReturnType<CollaborationPrivacyApiAdapter['getManagedBot']> => {
      void _signal;
      return { ...createBotDto(), bot_id: botId };
    },
  );
  const apiAdapter: CollaborationPrivacyApiAdapter = {
    listManagedBots,
    getManagedBot,
    patchManagedBot,
  };
  const getOrgUser = jest.fn(async (_userId: string, _signal?: AbortSignal) => {
    void _userId;
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
  const publishBotPublic = jest.fn(async (_botId: string, _userId: string, _body: unknown, _signal?: AbortSignal) => {
    void _botId;
    void _userId;
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
  const getWorkerConfig = jest.fn(async (botId: string, _signal?: AbortSignal) => {
    void _signal;
    return { success: true, worker_id: botId, fusion_enable: true, version: 1 };
  });
  const updateWorkerConfig = jest.fn(async (botId: string, body: { fusion_enable: boolean }, _signal?: AbortSignal) => {
    void _signal;
    return { success: true, worker_id: botId, fusion_enable: body.fusion_enable, version: 2 };
  });
  const dependencies: CollaborationPrivacyRuntimeDependencies = {
    apiAdapter,
    getOrgUser,
    listOrgDepts,
    publishBotPublic,
    taskGrant: {
      grantTaskClaim: jest.fn(() =>
        Promise.resolve({
          code: 200000,
          data: {
            bcs_bot_id: 'bot-real-1',
            api_key_prefix: 'pre',
            grant_status: 'granted',
            operator: 'u',
            gmt_modified: '',
          },
        }),
      ) as typeof grantTaskClaim,
      revokeTaskClaim: jest.fn(() =>
        Promise.resolve({
          code: 200000,
          data: {
            bcs_bot_id: 'bot-real-1',
            api_key_prefix: 'pre',
            grant_status: 'revoked',
            operator: 'u',
            gmt_modified: '',
          },
        }),
      ) as typeof revokeTaskClaim,
    },
    getWorkerConfig,
    updateWorkerConfig,
  };
  return {
    dependencies,
    apiAdapter,
    patchManagedBot,
    getManagedBot,
    listManagedBots,
    getOrgUser,
    listOrgDepts,
    publishBotPublic,
    getWorkerConfig,
    updateWorkerConfig,
  };
}

describe('collaboration privacy runtime wiring', () => {
  it('loads the page overview from ready APIs and does not request the Mock overview route', async () => {
    const { dependencies, listManagedBots, getManagedBot, getOrgUser, getWorkerConfig } = createDependencies();
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    const signal = new AbortController().signal;

    await expect(adapter.loadOverview('447147', signal)).resolves.toMatchObject({
      currentUser: {
        displayName: '真实用户',
        employeeNumber: '447147',
        departmentPath: ['蚂蚁集团-大安全-协作平台'],
      },
      bots: [
        expect.objectContaining({
          id: 'bot-real-1',
          name: '真实 Bot',
          profilePublic: true,
          profilePublicStatus: 'ready',
        }),
      ],
      organizationOptions: [],
    });

    expect(listManagedBots).toHaveBeenCalledWith({ kind: 'bot', user_id: '447147' }, signal);
    expect(getManagedBot).not.toHaveBeenCalled();
    expect(getOrgUser).toHaveBeenCalledWith('447147', signal);
    expect(getWorkerConfig).toHaveBeenCalledWith('bot-real-1', signal);
  });

  it('pads a lost numeric prefix before user lookup and keeps the canonical employee number downstream', async () => {
    const context = createDependencies();
    const { dependencies, getOrgUser, listManagedBots } = context;
    getOrgUser.mockImplementationOnce(async (userId: string, _signal?: AbortSignal) => {
      void _signal;
      return { code: 200000, data: { ...createUserDto(), user_id: userId } };
    });
    const adapter = createCollaborationPrivacyRuntimeAdapter({
      ...dependencies,
      getOrgUser,
      apiAdapter: {
        ...dependencies.apiAdapter,
        listManagedBots,
      },
    });

    await expect(adapter.loadOverview('12345')).resolves.toMatchObject({
      currentUser: { employeeNumber: '012345' },
    });

    expect(getOrgUser).toHaveBeenCalledWith('012345', undefined);
    expect(listManagedBots).toHaveBeenCalledWith({ kind: 'bot', user_id: '012345' }, undefined);
  });

  it('normalizes a short numeric user_id returned by the user API before page echo', async () => {
    const { dependencies, getOrgUser } = createDependencies();
    getOrgUser.mockImplementationOnce(async (_userId: string, _signal?: AbortSignal) => {
      void _userId;
      void _signal;
      return { code: 200000, data: { ...createUserDto(), user_id: '1234' } };
    });
    const adapter = createCollaborationPrivacyRuntimeAdapter({ ...dependencies, getOrgUser });

    await expect(adapter.syncDepartment('1234')).resolves.toMatchObject({ employeeNumber: '001234' });
    expect(getOrgUser).toHaveBeenCalledWith('001234', undefined);
  });

  it('treats a missing BCSFuse config as an unavailable profile capability without blocking the overview', async () => {
    const { dependencies, getWorkerConfig } = createDependencies();
    getWorkerConfig.mockRejectedValueOnce(
      new BackendRequestError('Not Found', {
        status: 404,
        apiPath: '/openapi/v1/bcsfuse/workers/bot-real-1/config',
      }),
    );
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);

    await expect(adapter.loadOverview('447147')).resolves.toMatchObject({
      currentUser: { employeeNumber: '447147' },
      bots: [expect.objectContaining({ profilePublic: false, profilePublicStatus: 'unavailable' })],
    });
  });

  it('does not block the overview when one Bot profile config is unavailable', async () => {
    const { dependencies, getWorkerConfig } = createDependencies();
    getWorkerConfig.mockRejectedValueOnce(new Error('BCSFuse unavailable'));
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);

    await expect(adapter.loadOverview('447147')).resolves.toMatchObject({
      currentUser: { employeeNumber: '447147' },
      bots: [expect.objectContaining({ id: 'bot-real-1', profilePublic: false, profilePublicStatus: 'unavailable' })],
    });
  });

  it('starts BCSFuse profile reads without waiting for department hydration', async () => {
    const { dependencies, getWorkerConfig, listOrgDepts } = createDependencies();
    const departmentResult = { code: 200000, data: [] as OrgDeptDto[] };
    let releaseDepartments!: (value: typeof departmentResult) => void;
    const pendingDepartments = new Promise<typeof departmentResult>((resolve) => {
      releaseDepartments = resolve;
    });
    listOrgDepts.mockImplementation(async () => pendingDepartments);
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);

    const loadPromise = adapter.loadOverview('447147');
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 0);
    });

    expect(getWorkerConfig).toHaveBeenCalledWith('bot-real-1', undefined);
    releaseDepartments(departmentResult);
    await expect(loadPromise).resolves.toMatchObject({
      bots: [expect.objectContaining({ id: 'bot-real-1', profilePublic: true, profilePublicStatus: 'ready' })],
    });
  });

  it('propagates cancellation from a profile config request instead of masking page teardown', async () => {
    const { dependencies, getWorkerConfig } = createDependencies();
    const abortError = Object.assign(new Error('Aborted'), { name: 'AbortError' });
    getWorkerConfig.mockRejectedValueOnce(abortError);
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    const controller = new AbortController();
    controller.abort();

    await expect(adapter.loadOverview('447147', controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('refreshes one Bot through the detail endpoint without reloading the managed Bot list', async () => {
    const { dependencies, getManagedBot, listManagedBots } = createDependencies();
    getManagedBot.mockResolvedValue({
      ...createBotDto(),
      status: 'hidden',
      user_visibility: 'public',
    });
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    await adapter.loadOverview('447147');
    listManagedBots.mockClear();
    const signal = new AbortController().signal;

    await expect(adapter.refreshManagedBot('bot-real-1', signal)).resolves.toMatchObject({
      id: 'bot-real-1',
      engine: 'OpenClaw',
      collaborationStatus: 'hidden',
      publication: { user: { scope: 'all', organizationPaths: [] } },
    });

    expect(getManagedBot).toHaveBeenCalledWith('bot-real-1', signal);
    expect(listManagedBots).not.toHaveBeenCalled();
  });

  it('hydrates configured department codes to complete names once and preserves original delimiters', async () => {
    const { dependencies, apiAdapter, listOrgDepts } = createDependencies();
    apiAdapter.listManagedBots = jest.fn(
      async (_params = {}, signal?: AbortSignal): ReturnType<CollaborationPrivacyApiAdapter['listManagedBots']> => {
        void _params;
        void signal;
        return {
          items: [
            {
              ...createBotDto(),
              visibility: 'public',
              user_visibility: 'public',
              friend_ext: {
                view_scope_user_friend_deps: ['A4195'],
                view_scope_agent_friend_deps: ['A5000'],
                no_check_scope_friend_deps: ['A4195'],
              },
              friend_check_in_strategy: 'DEPT_FREE',
            } satisfies CollaborationBotDto,
          ],
          total: 1,
          offset: 0,
          limit: 20,
        };
      },
    );
    listOrgDepts.mockImplementation(async ({ keyword }: { keyword?: string }, signal?: AbortSignal) => {
      void signal;
      const data: Record<string, OrgDeptDto> = {
        A4195: {
          dept_no: 'A4195',
          dept_name: '示例集团-技术事业部-平台团队',
          dept_path: 'ROOT/A4195',
        },
        A5000: {
          dept_no: 'A5000',
          dept_name: '示例集团 / 智能事业部 / 算法团队',
          dept_path: 'ROOT/A5000',
        },
      };
      const department = keyword ? data[keyword] : undefined;
      return { code: 200000, data: department ? [department] : [] };
    });
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    const signal = new AbortController().signal;

    await expect(adapter.loadOverview('447147', signal)).resolves.toMatchObject({
      bots: [
        {
          publication: {
            user: {
              scope: 'restricted',
              organizationPaths: [['示例集团-技术事业部-平台团队']],
              organizationEntries: [{ deptNo: 'A4195', path: ['示例集团-技术事业部-平台团队'] }],
            },
            bot: {
              scope: 'restricted',
              organizationPaths: [['示例集团 / 智能事业部 / 算法团队']],
              organizationEntries: [{ deptNo: 'A5000', path: ['示例集团 / 智能事业部 / 算法团队'] }],
            },
          },
          friendApproval: {
            mode: 'partial_exempt',
            exemptOrganizationPaths: [['示例集团-技术事业部-平台团队']],
            exemptDepartmentNos: ['A4195'],
            exemptOrganizationEntries: [{ deptNo: 'A4195', path: ['示例集团-技术事业部-平台团队'] }],
          },
        },
      ],
    });
    expect(listOrgDepts).toHaveBeenCalledTimes(2);
    expect(listOrgDepts).toHaveBeenCalledWith({ keyword: 'A4195' }, signal);
    expect(listOrgDepts).toHaveBeenCalledWith({ keyword: 'A5000' }, signal);
  });

  it('hydrates the current dual-audience publication state from the real managed Bot fields', async () => {
    const { dependencies, apiAdapter } = createDependencies();
    apiAdapter.listManagedBots = jest.fn(async () => ({
      items: [
        {
          ...createBotDto(),
          bot_id: '20260715_vl4oht43:447147',
          visibility: 'public',
          user_visibility: 'private',
          friend_ext: {},
        } satisfies CollaborationBotDto,
      ],
      total: 1,
      offset: 0,
      limit: 20,
    }));
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);

    await expect(adapter.loadOverview('447147')).resolves.toMatchObject({
      bots: [
        {
          id: '20260715_vl4oht43:447147',
          publication: {
            user: { scope: 'none', organizationPaths: [] },
            bot: { scope: 'all', organizationPaths: [] },
          },
        },
      ],
    });
  });

  it('restores pending publication status and approval links from the managed Bot snapshot', async () => {
    const { dependencies, apiAdapter } = createDependencies();
    apiAdapter.listManagedBots = jest.fn(async () => ({
      items: [
        {
          ...createBotDto(),
          friend_ext: {
            public_user_approval: {
              puid: 'user-puid',
              status: 'CREATED',
              visibility: 'public',
              approval_url: 'https://approval.example.com/ticket/dispatch/user-puid',
              view_friend_deps: [],
            },
          },
        },
      ],
      total: 1,
      offset: 0,
      limit: 20,
    }));
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);

    await expect(adapter.loadOverview('447147')).resolves.toMatchObject({
      bots: [
        {
          id: 'bot-real-1',
          pendingPublications: {
            user: {
              id: 'user-puid',
              audience: 'user',
              approvalUrl: 'https://approval.example.com/ticket/dispatch/user-puid',
            },
          },
        },
      ],
    });
  });

  it('does not expose the current Human collaboration identity as a configurable Bot', async () => {
    const { dependencies, apiAdapter } = createDependencies();
    apiAdapter.listManagedBots = jest.fn(async () => ({
      items: [
        {
          kind: 'human',
          bot_id: 'human_447147',
          name: '真实用户',
          visibility: 'private',
          status: 'online',
          env: 'pre',
          created_at: 1,
          updated_at: 2,
        } satisfies CollaborationBotDto,
        createBotDto(),
      ],
      total: 2,
      offset: 0,
      limit: 20,
    }));
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);

    await expect(adapter.loadOverview('447147')).resolves.toMatchObject({
      bots: [expect.objectContaining({ id: 'bot-real-1' })],
    });
  });

  it('routes ready mutations and department search to real controllers', async () => {
    const { dependencies, apiAdapter, listOrgDepts, publishBotPublic } = createDependencies();
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    await adapter.loadOverview('447147');

    const signal = new AbortController().signal;
    await adapter.syncDepartment('447147', signal);
    await adapter.searchDepartments('协作', signal);
    await adapter.updateDirectSetting({ botId: 'bot-real-1', setting: 'profilePublic', value: true }, signal);
    const publicationResult = await adapter.submitPublication(
      {
        botId: 'bot-real-1',
        audience: 'user',
        config: { scope: 'restricted', organizationPaths: [['蚂蚁集团', '大安全', '协作平台']] },
        deptEntries: [{ deptNo: 'A4195', deptName: '蚂蚁集团-大安全-协作平台' }],
      },
      signal,
    );

    expect(publicationResult).toMatchObject({
      status: 'pending',
      publication: { approvalUrl: '/admin/work-orders/puid-1' },
    });
    expect(dependencies.updateWorkerConfig).toHaveBeenCalledWith('bot-real-1', { fusion_enable: true }, signal);
    expect(apiAdapter.patchManagedBot).not.toHaveBeenCalledWith('bot-real-1', { visibility: 'public' }, signal);
    expect(listOrgDepts).toHaveBeenCalledWith({ keyword: '协作' }, signal);
    expect(publishBotPublic).toHaveBeenCalledWith(
      'bot-real-1',
      '447147',
      {
        public_scope: 'user',
        visibility: 'public',
        view_depts: [{ deptNo: 'A4195', deptName: '蚂蚁集团-大安全-协作平台' }],
      },
      signal,
    );
  });

  it('uses the BCSFuse response as the profile setting result and does not patch visibility', async () => {
    const { dependencies, apiAdapter, updateWorkerConfig } = createDependencies();
    updateWorkerConfig.mockResolvedValueOnce({
      success: true,
      worker_id: 'bot-real-1',
      fusion_enable: false,
      version: 2,
    });
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    await adapter.loadOverview('447147');

    await expect(
      adapter.updateDirectSetting({ botId: 'bot-real-1', setting: 'profilePublic', value: true }),
    ).resolves.toBe(false);
    expect(updateWorkerConfig).toHaveBeenLastCalledWith('bot-real-1', { fusion_enable: true }, undefined);
    expect(apiAdapter.patchManagedBot).not.toHaveBeenCalledWith('bot-real-1', { visibility: 'public' }, undefined);
  });

  it('maps Bot all-public to agent/public without department restrictions', async () => {
    const { dependencies, publishBotPublic } = createDependencies();
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    await adapter.loadOverview('447147');
    const signal = new AbortController().signal;

    await adapter.submitPublication(
      {
        botId: 'bot-real-1',
        audience: 'bot',
        config: { scope: 'all', organizationPaths: [] },
      },
      signal,
    );

    expect(publishBotPublic).toHaveBeenCalledWith(
      'bot-real-1',
      '447147',
      {
        public_scope: 'agent',
        visibility: 'public',
        view_depts: null,
      },
      signal,
    );
  });

  it('treats a CREATED approval ticket as a pending publication', async () => {
    const { dependencies } = createDependencies();
    dependencies.publishBotPublic = jest.fn(async () => ({
      code: 200000,
      message: 'OK',
      data: {
        success: true,
        puid: 'antprocess-agentclaw_botpublic_20260715-vl4oht43-44714720260825190956',
        approval_url: 'https://approval.example.com/ticket/dispatch/publication-20260715-vl4oht43-44714720260825190956',
        state: 'CREATED' as const,
        last_operate: null,
        error_msg: null,
        lastOperate: null,
      },
      request_id: '0be8c63017876561947632931e7aaa',
    }));
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    await adapter.loadOverview('447147');

    await expect(
      adapter.submitPublication({
        botId: 'bot-real-1',
        audience: 'user',
        config: { scope: 'all', organizationPaths: [] },
      }),
    ).resolves.toMatchObject({
      status: 'pending',
      publication: {
        id: 'antprocess-agentclaw_botpublic_20260715-vl4oht43-44714720260825190956',
        audience: 'user',
        approvalUrl: 'https://approval.example.com/ticket/dispatch/publication-20260715-vl4oht43-44714720260825190956',
      },
    });
  });

  it('drops an unsafe approval URL instead of exposing it to the page', async () => {
    const { dependencies } = createDependencies();
    dependencies.publishBotPublic = jest.fn(async () => ({
      code: 200000,
      data: {
        success: true,
        puid: 'puid-unsafe',
        approval_url: 'javascript:alert(1)',
        state: 'PROCESSING' as const,
      },
    }));
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    await adapter.loadOverview('447147');

    const result = await adapter.submitPublication({
      botId: 'bot-real-1',
      audience: 'bot',
      config: { scope: 'all', organizationPaths: [] },
    });

    expect(result).toEqual({
      status: 'pending',
      publication: {
        id: 'puid-unsafe',
        audience: 'bot',
        target: { scope: 'all', organizationPaths: [] },
        submittedAt: expect.any(String),
      },
    });
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
    await adapter.loadOverview('447147');

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

  it('applies a private visibility withdrawal immediately even when the response includes an approval-like state', async () => {
    const { dependencies } = createDependencies();
    dependencies.publishBotPublic = jest.fn(async () => ({
      code: 200000,
      data: {
        success: true,
        puid: 'puid-private-withdrawal',
        approval_url: null,
        state: 'PROCESSING' as const,
        last_operate: null,
        error_msg: null,
        visibility: 'private',
        visibility_field: 'visibility',
      },
    }));
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    await adapter.loadOverview('447147');

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

  it('accepts the private direct-update response when no approval ticket state is returned', async () => {
    const { dependencies } = createDependencies();
    dependencies.publishBotPublic = jest.fn(async () => ({
      code: 200000,
      data: {
        success: true,
        puid: null,
        approval_url: null,
        state: null,
        last_operate: null,
        error_msg: null,
      },
    }));
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    await adapter.loadOverview('447147');

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

  it('routes task-claim and dream toggles through their correct paths', async () => {
    const { dependencies, patchManagedBot } = createDependencies();
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    await adapter.loadOverview('447147');

    await expect(
      adapter.updateDirectSetting({ botId: 'bot-real-1', setting: 'taskClaimingEnabled', value: true }),
    ).rejects.toThrow('任务认领开关请通过 enable/disableTaskClaim 提交(grant/revoke + PATCH task_claim_mode 双写)');
    expect(patchManagedBot).not.toHaveBeenCalled();

    await expect(
      adapter.updateDirectSetting({ botId: 'bot-real-1', setting: 'dreamModelEnabled', value: true }),
    ).resolves.toBe(true);
    expect(patchManagedBot).toHaveBeenCalledWith('bot-real-1', { task_dream_mode: true }, undefined);
  });

  it('double-writes claim on (grant + PATCH) and accepts when both succeed', async () => {
    const { dependencies } = createDependencies();
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    await adapter.loadOverview('447147');

    const updated = await adapter.enableTaskClaim('bot-real-1');
    expect(updated.taskClaimingEnabled).toBe(true);
    expect(updated.taskClaimStatus).toBe('authorized');
    expect(dependencies.taskGrant.grantTaskClaim).toHaveBeenCalledWith({ bcs_bot_id: 'bot-real-1' }, undefined);
    expect(dependencies.apiAdapter.patchManagedBot).toHaveBeenCalledWith(
      'bot-real-1',
      { task_claim_mode: true },
      undefined,
    );
  });

  it('rolls back the grant when the claim-on PATCH fails', async () => {
    const { dependencies, patchManagedBot } = createDependencies();
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    await adapter.loadOverview('447147');

    patchManagedBot.mockRejectedValueOnce(new Error('patch down'));
    await expect(adapter.enableTaskClaim('bot-real-1')).rejects.toThrow('patch down');
    expect(dependencies.taskGrant.revokeTaskClaim).toHaveBeenCalledWith({ bcs_bot_id: 'bot-real-1' }, undefined);
  });

  it('patches friend approval through the real Bot controller while preserving all existing friend_ext fields', async () => {
    const { dependencies, patchManagedBot } = createDependencies();
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    await adapter.loadOverview('447147');
    const signal = new AbortController().signal;
    const config: FriendApprovalConfig = {
      mode: 'partial_exempt',
      exemptOrganizationPaths: [['蚂蚁集团', '大安全', '协作平台']],
      exemptDepartmentNos: [' A4195 ', 'A4195', 'A5000'],
    };

    await expect(adapter.updateFriendApproval({ botId: 'bot-real-1', config }, signal)).resolves.toEqual({
      mode: 'partial_exempt',
      exemptOrganizationPaths: [['蚂蚁集团', '大安全', '协作平台']],
      exemptDepartmentNos: ['A4195', 'A5000'],
    });

    expect(patchManagedBot).toHaveBeenCalledWith(
      'bot-real-1',
      {
        friend_ext: {
          public_user_approval: { puid: 'p-user', status: 'AGREE' },
          view_scope_agent_friend_deps: ['A1000'],
          no_check_scope_friend_deps: ['A4195', 'A5000'],
        },
        friend_check_in_strategy: 'DEPT_FREE',
      },
      signal,
    );
  });

  it('updates the cached friend_ext snapshot after a successful PATCH without dropping omitted subfields', async () => {
    const { dependencies, patchManagedBot } = createDependencies();
    patchManagedBot.mockResolvedValueOnce({
      ...createBotDto(),
      friend_ext: { no_check_scope_friend_deps: ['D1'] },
      friend_check_in_strategy: 'DEPT_FREE',
    });
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    await adapter.loadOverview('447147');

    await adapter.updateFriendApproval({
      botId: 'bot-real-1',
      config: {
        mode: 'partial_exempt',
        exemptOrganizationPaths: [['研发一部']],
        exemptDepartmentNos: ['D1'],
      },
    });
    await adapter.updateFriendApproval({
      botId: 'bot-real-1',
      config: { mode: 'all', exemptOrganizationPaths: [] },
    });

    expect(patchManagedBot).toHaveBeenNthCalledWith(
      2,
      'bot-real-1',
      {
        friend_ext: {
          public_user_approval: { puid: 'p-user', status: 'AGREE' },
          view_scope_agent_friend_deps: ['A1000'],
          no_check_scope_friend_deps: [],
        },
        friend_check_in_strategy: 'APPROVAL',
      },
      undefined,
    );
  });

  it('fails closed when friend approval is submitted before overview load or for an unknown Bot', async () => {
    const { dependencies, patchManagedBot } = createDependencies();
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    const config: FriendApprovalConfig = { mode: 'all', exemptOrganizationPaths: [] };

    await expect(adapter.updateFriendApproval({ botId: 'bot-real-1', config })).rejects.toThrow('协作权限数据尚未加载');
    await adapter.loadOverview('447147');
    await expect(adapter.updateFriendApproval({ botId: 'missing-bot', config })).rejects.toThrow(
      '未找到要更新好友审批策略的 Bot',
    );
    expect(patchManagedBot).not.toHaveBeenCalled();
  });

  it('rejects incomplete or inconsistent friend approval PATCH responses without replacing the snapshot', async () => {
    const invalidResponses: CollaborationBotDto[] = [
      { ...createBotDto(), friend_ext: undefined, friend_check_in_strategy: 'APPROVAL' },
      { ...createBotDto(), friend_check_in_strategy: undefined },
      {
        ...createBotDto(),
        friend_ext: { ...createBotDto().friend_ext, no_check_scope_friend_deps: ['D1'] },
        friend_check_in_strategy: 'OPEN',
      },
      {
        ...createBotDto(),
        friend_ext: { ...createBotDto().friend_ext, no_check_scope_friend_deps: ['D2'] },
        friend_check_in_strategy: 'DEPT_FREE',
      },
    ];

    for (const response of invalidResponses) {
      const { dependencies, patchManagedBot } = createDependencies();
      patchManagedBot.mockResolvedValueOnce(response);
      const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
      await adapter.loadOverview('447147');

      await expect(
        adapter.updateFriendApproval({
          botId: 'bot-real-1',
          config: {
            mode: 'partial_exempt',
            exemptOrganizationPaths: [['研发一部']],
            exemptDepartmentNos: ['D1'],
          },
        }),
      ).rejects.toThrow('Bot 更新接口未返回已生效的好友审批策略');

      await adapter.updateFriendApproval({
        botId: 'bot-real-1',
        config: { mode: 'none', exemptOrganizationPaths: [] },
      });

      expect(patchManagedBot).toHaveBeenLastCalledWith(
        'bot-real-1',
        {
          friend_ext: {
            public_user_approval: { puid: 'p-user', status: 'AGREE' },
            view_scope_agent_friend_deps: ['A1000'],
            no_check_scope_friend_deps: [],
          },
          friend_check_in_strategy: 'OPEN',
        },
        undefined,
      );
    }
  });

  it('does not replace the cached snapshot when the friend approval PATCH fails', async () => {
    const { dependencies, patchManagedBot } = createDependencies();
    patchManagedBot.mockRejectedValueOnce(new Error('network failed'));
    const adapter = createCollaborationPrivacyRuntimeAdapter(dependencies);
    await adapter.loadOverview('447147');

    await expect(
      adapter.updateFriendApproval({
        botId: 'bot-real-1',
        config: {
          mode: 'partial_exempt',
          exemptOrganizationPaths: [['研发一部']],
          exemptDepartmentNos: ['D1'],
        },
      }),
    ).rejects.toThrow('network failed');

    await adapter.updateFriendApproval({
      botId: 'bot-real-1',
      config: { mode: 'none', exemptOrganizationPaths: [] },
    });

    expect(patchManagedBot).toHaveBeenLastCalledWith(
      'bot-real-1',
      {
        friend_ext: {
          public_user_approval: { puid: 'p-user', status: 'AGREE' },
          view_scope_agent_friend_deps: ['A1000'],
          no_check_scope_friend_deps: [],
        },
        friend_check_in_strategy: 'OPEN',
      },
      undefined,
    );
  });
});
