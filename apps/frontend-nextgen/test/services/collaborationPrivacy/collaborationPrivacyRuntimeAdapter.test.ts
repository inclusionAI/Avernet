import type { BcsPublishResult, CollaborationBotDto, OrgUserDto } from '@/services/backendApi';
import { createCollaborationPrivacyApiAdapter } from '@/services/collaborationPrivacy/collaborationPrivacyApiAdapter';
import type { PublicationCommand } from '@/services/collaborationPrivacy/collaborationPrivacyGateway';
import { createCollaborationPrivacyRuntimeAdapter } from '@/services/collaborationPrivacy/collaborationPrivacyRuntimeAdapter';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

const orgUser: OrgUserDto = {
  user_id: '900004',
  username: 'testuser',
  display_name: '测试用户',
  full_name: '测试用户',
  tenant: 'ant',
  dept_no: 'A4195',
  dept_name: '全网巡检',
  dept_path: '00001/A4195',
};

/**
 * 构造一个已加载空 Overview 的运行期适配器,便于只聚焦 submitPublication 的终态映射。
 * publishBotPublic 固定返回给定结果,不触达真实网络。
 */
function createAdapterWithPublishResult(
  publishResult: BcsPublishResult,
  profile?: {
    getUserProfilePresentation: () => { preferAuthenticatedUserProfile: boolean; showDepartment: boolean };
    getHumanIdentity: () => { userId: string; displayName: string; online: boolean } | null;
  },
) {
  const listMyBots = jest.fn<(...args: any[]) => any>();
  const getCollaborationBot = jest.fn<(...args: any[]) => any>();
  const patchCollaborationBot = jest.fn<(...args: any[]) => any>();
  const apiAdapter = createCollaborationPrivacyApiAdapter({
    listMyBots,
    getCollaborationBot,
    patchCollaborationBot,
  });
  listMyBots.mockResolvedValue({ code: 20000, data: { items: [], total: 0, offset: 0, limit: 20 } });

  const getOrgUser = jest.fn<(...args: any[]) => any>();
  getOrgUser.mockResolvedValue({ success: true, data: orgUser });
  const listOrgDepts = jest.fn<(...args: any[]) => any>();
  const publishBotPublic = jest.fn<(...args: any[]) => any>();
  publishBotPublic.mockResolvedValue({ success: true, data: publishResult });
  const getWorkerConfig = jest.fn<(...args: any[]) => any>();
  const updateWorkerConfig = jest.fn<(...args: any[]) => any>();
  const grantTaskClaim = jest.fn<(...args: any[]) => any>();
  const revokeTaskClaim = jest.fn<(...args: any[]) => any>();

  const adapter = createCollaborationPrivacyRuntimeAdapter({
    apiAdapter,
    getOrgUser,
    listOrgDepts,
    publishBotPublic,
    taskGrant: { grantTaskClaim, revokeTaskClaim },
    getWorkerConfig,
    updateWorkerConfig,
    ...profile,
  });
  return { adapter, getOrgUser, listOrgDepts, listMyBots, publishBotPublic };
}

/** 外部 Bot 合同标准响应形态的企业 Bot DTO（apiAdapter 运行时校验可通过）。 */
function createExternalBotDto(overrides: Record<string, unknown> = {}): CollaborationBotDto {
  return {
    kind: 'bot',
    bot_id: 'external-1',
    name: '外部助手',
    status: 'online',
    visibility: 'private',
    user_visibility: 'private',
    env: 'prod',
    descriptor: { summary: '', domains: [], scopes: [], skills: [] },
    reachability: 'reachable',
    friend_ext: {},
    friend_check_in_strategy: 'APPROVAL',
    created_at: 1792108800000,
    updated_at: 1792112400000,
    ...overrides,
  } as CollaborationBotDto;
}

const externalScope = { target: 'activeBot', botId: 'external-1', external: true } as const;

/** 外部 Bot 场景的运行期适配器夹具：mine 命中单个外部 Bot，直接暴露 GET/PATCH 协作 Bot 控制器。 */
function createExternalAdapter() {
  const listMyBots = jest.fn<(...args: any[]) => any>();
  const getCollaborationBot = jest.fn<(...args: any[]) => any>();
  const patchCollaborationBot = jest.fn<(...args: any[]) => any>();
  const apiAdapter = createCollaborationPrivacyApiAdapter({
    listMyBots,
    getCollaborationBot,
    patchCollaborationBot,
  });
  const getOrgUser = jest.fn<(...args: any[]) => any>();
  getOrgUser.mockResolvedValue({ success: true, data: orgUser });
  const listOrgDepts = jest.fn<(...args: any[]) => any>();
  const publishBotPublic = jest.fn<(...args: any[]) => any>();
  const getWorkerConfig = jest.fn<(...args: any[]) => any>();
  getWorkerConfig.mockResolvedValue({ worker_id: 'external-1', fusion_enable: false });
  const adapter = createCollaborationPrivacyRuntimeAdapter({
    apiAdapter,
    getOrgUser,
    listOrgDepts,
    publishBotPublic,
    taskGrant: { grantTaskClaim: jest.fn(), revokeTaskClaim: jest.fn() },
    getWorkerConfig,
    updateWorkerConfig: jest.fn(),
  });
  return { adapter, listMyBots, getCollaborationBot, patchCollaborationBot, listOrgDepts, publishBotPublic };
}

/** 让 mine 命中给定的外部 Bot，返回形如已部署 BCS 的标准信封（code 20000）。 */
function mockMineWithExternalBot(fixture: { dto: CollaborationBotDto }) {
  return {
    code: 20000,
    message: 'OK',
    data: { items: [fixture.dto], total: 1, offset: 0, limit: 20 },
  };
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe('collaborationPrivacyRuntimeAdapter Open Core user profile', () => {
  it('使用认证 User 名称并跳过组织 User 接口与部门同步请求', async () => {
    const { adapter, getOrgUser, listMyBots } = createAdapterWithPublishResult(
      {
        success: true,
        puid: 'unused',
        approval_url: null,
        state: 'SKIPPED',
        last_operate: null,
        visibility: 'public',
        visibility_field: null,
        error_msg: null,
      },
      {
        getUserProfilePresentation: () => ({ preferAuthenticatedUserProfile: true, showDepartment: false }),
        getHumanIdentity: () => ({ userId: 'external-user-1', displayName: '开源用户', online: true }),
      },
    );

    const overview = await adapter.loadOverview('external-user-1');
    const synced = await adapter.syncDepartment('external-user-1');

    expect(getOrgUser).not.toHaveBeenCalled();
    expect(listMyBots).toHaveBeenCalledWith({ kind: 'bot' }, undefined);
    expect(overview.currentUser).toMatchObject({
      displayName: '开源用户',
      employeeNumber: 'external-user-1',
      departmentPath: [],
    });
    expect(synced).toMatchObject({ displayName: '开源用户', employeeNumber: 'external-user-1', departmentPath: [] });
  });
});

describe('collaborationPrivacyRuntimeAdapter.submitPublication', () => {
  it('把 SKIPPED 终态映射为已完成(跳过审批直接通过),且不携带审批链接', async () => {
    const { adapter } = createAdapterWithPublishResult({
      success: true,
      puid: 'pub-skip-1',
      approval_url: null,
      state: 'SKIPPED',
      last_operate: null,
      visibility: 'public',
      visibility_field: null,
      error_msg: null,
    });
    await adapter.loadOverview('900004');

    const command: PublicationCommand = {
      botId: 'bot-1',
      audience: 'user',
      config: { scope: 'all', organizationPaths: [] },
    };
    const result = await adapter.submitPublication(command);

    expect(result).toEqual({ status: 'completed', config: { scope: 'all', organizationPaths: [] } });
    expect((result as { approvalUrl?: string }).approvalUrl).toBeUndefined();
  });

  it('限定组织可申请遇到 SKIPPED 时,按本次请求的受限配置直接生效', async () => {
    const { adapter } = createAdapterWithPublishResult({
      success: true,
      puid: 'pub-skip-2',
      approval_url: '',
      state: 'SKIPPED',
      last_operate: null,
      visibility: 'public',
      visibility_field: 'view',
      error_msg: null,
    });
    await adapter.loadOverview('900004');

    const result = await adapter.submitPublication({
      botId: 'bot-1',
      audience: 'bot',
      config: { scope: 'restricted', organizationPaths: [] },
      deptEntries: [{ deptNo: 'A4195', deptName: '全网巡检' }],
    });

    expect(result).toEqual({ status: 'completed', config: { scope: 'restricted', organizationPaths: [] } });
    expect((result as { approvalUrl?: string }).approvalUrl).toBeUndefined();
  });

  it('提交可见性时按 scope 映射 visibility:限定组织可申请→protected、全部可见→public、不可见→private(对用户与对 Bot 一致)', async () => {
    const { adapter, publishBotPublic } = createAdapterWithPublishResult({
      success: true,
      puid: 'pub-body',
      approval_url: null,
      state: 'SKIPPED',
      last_operate: null,
      visibility: 'public',
      visibility_field: null,
      error_msg: null,
    });
    await adapter.loadOverview('900004');

    // 全部可见 → visibility = public
    await adapter.submitPublication({ botId: 'bot-1', audience: 'user', config: { scope: 'all', organizationPaths: [] } });
    expect(publishBotPublic).toHaveBeenLastCalledWith(
      'bot-1',
      '900004',
      expect.objectContaining({ public_scope: 'user', visibility: 'public', view_depts: null }),
      undefined,
    );

    // 限定组织可申请 → visibility = protected（对 Bot 受众同样传 protected）
    await adapter.submitPublication({
      botId: 'bot-1',
      audience: 'bot',
      config: { scope: 'restricted', organizationPaths: [['全网巡检']] },
      deptEntries: [{ deptNo: 'A4195', deptName: '全网巡检' }],
    });
    expect(publishBotPublic).toHaveBeenLastCalledWith(
      'bot-1',
      '900004',
      expect.objectContaining({
        public_scope: 'agent',
        visibility: 'protected',
        view_depts: [{ deptNo: 'A4195', deptName: '全网巡检' }],
      }),
      undefined,
    );

    // 不可见 → visibility = private
    await adapter.submitPublication({ botId: 'bot-1', audience: 'user', config: { scope: 'none', organizationPaths: [] } });
    expect(publishBotPublic).toHaveBeenLastCalledWith(
      'bot-1',
      '900004',
      expect.objectContaining({ public_scope: 'user', visibility: 'private', view_depts: null }),
      undefined,
    );
  });
});

describe('collaborationPrivacyRuntimeAdapter.externalBotPublication', () => {
  it('全部可见/不可见直改 PATCH 且不触发 publish 审批链路', async () => {
    const { adapter, listMyBots, getCollaborationBot, patchCollaborationBot, publishBotPublic } = createExternalAdapter();
    listMyBots.mockResolvedValue(
      mockMineWithExternalBot({ dto: createExternalBotDto({ visibility: 'protected' }) }),
    );
    patchCollaborationBot.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: createExternalBotDto({ visibility: 'public' }),
    });
    await adapter.loadOverview('900004', undefined, externalScope);

    const published = await adapter.submitPublication({
      botId: 'external-1',
      audience: 'bot',
      isExternalBot: true,
      config: { scope: 'all', organizationPaths: [] },
    });

    expect(patchCollaborationBot).toHaveBeenCalledTimes(1);
    expect(patchCollaborationBot).toHaveBeenCalledWith('external-1', { visibility: 'public' }, undefined);
    expect(publishBotPublic).not.toHaveBeenCalled();
    expect(getCollaborationBot).not.toHaveBeenCalled();
    expect(published).toEqual({ status: 'completed', config: { scope: 'all', organizationPaths: [] } });

    // 不可见 → visibility = private（与既有 none→private 合同一致）
    await adapter.submitPublication({
      botId: 'external-1',
      audience: 'bot',
      isExternalBot: true,
      config: { scope: 'none', organizationPaths: [] },
    });
    expect(patchCollaborationBot).toHaveBeenLastCalledWith('external-1', { visibility: 'private' }, undefined);
    expect(publishBotPublic).not.toHaveBeenCalled();
  });

  it('限定组织先 GET 同一 Bot 的最新 friend_ext，整体替换只改部门范围键并保留全部未知键', async () => {
    const { adapter, listMyBots, getCollaborationBot, patchCollaborationBot, publishBotPublic } = createExternalAdapter();
    listMyBots.mockResolvedValue(
      mockMineWithExternalBot({
        dto: createExternalBotDto({
          visibility: 'public',
          friend_ext: {
            view_scope_agent_friend_deps: ['旧范围'],
            no_check_scope_friend_deps: ['D9'],
            public_agent_approval: { status: 'PENDING', puid: 'PU-9', approval_url: 'https://example.com/x' },
            custom_flags: { nested: { keep: true } },
          },
        }),
      }),
    );
    getCollaborationBot.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: createExternalBotDto({
        visibility: 'public',
        friend_ext: {
          view_scope_agent_friend_deps: ['旧范围'],
          no_check_scope_friend_deps: ['D9'],
          public_agent_approval: { status: 'PENDING', puid: 'PU-9', approval_url: 'https://example.com/x' },
          custom_flags: { nested: { keep: true } },
        },
      }),
    });
    patchCollaborationBot.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: createExternalBotDto({
        visibility: 'protected',
        friend_ext: {
          view_scope_agent_friend_deps: ['蚂蚁集团-CRO线-大安全技术部'],
          no_check_scope_friend_deps: ['D9'],
          public_agent_approval: { status: 'PENDING', puid: 'PU-9', approval_url: 'https://example.com/x' },
          custom_flags: { nested: { keep: true } },
        },
      }),
    });
    await adapter.loadOverview('900004', undefined, externalScope);

    const result = await adapter.submitPublication({
      botId: 'external-1',
      audience: 'bot',
      isExternalBot: true,
      config: { scope: 'restricted', organizationPaths: [['蚂蚁集团-CRO线-大安全技术部']] },
      deptEntries: [{ deptNo: 'A4195', deptName: '蚂蚁集团-CRO线-大安全技术部' }],
    });

    expect(getCollaborationBot).toHaveBeenCalledWith('external-1', undefined);
    // GET 先于 PATCH 执行（friend_ext 必须取最新值，防止覆盖期间丢了别人的写入）。
    expect(getCollaborationBot.mock.invocationCallOrder[0]).toBeLessThan(patchCollaborationBot.mock.invocationCallOrder[0]);
    expect(patchCollaborationBot).toHaveBeenCalledWith(
      'external-1',
      {
        visibility: 'protected',
        friend_ext: {
          view_scope_agent_friend_deps: ['蚂蚁集团-CRO线-大安全技术部'],
          no_check_scope_friend_deps: ['D9'],
          public_agent_approval: { status: 'PENDING', puid: 'PU-9', approval_url: 'https://example.com/x' },
          custom_flags: { nested: { keep: true } },
        },
      },
      undefined,
    );
    expect(publishBotPublic).not.toHaveBeenCalled();
    expect(result).toEqual({
      status: 'completed',
      config: { scope: 'restricted', organizationPaths: [['蚂蚁集团-CRO线-大安全技术部']] },
    });
  });

  it('限定组织的部门名称缺失时保存前直接拒绝，不发 GET/PATCH', async () => {
    const { adapter, listMyBots, getCollaborationBot, patchCollaborationBot } = createExternalAdapter();
    listMyBots.mockResolvedValue(mockMineWithExternalBot({ dto: createExternalBotDto({ visibility: 'private' }) }));
    await adapter.loadOverview('900004', undefined, externalScope);

    await expect(
      adapter.submitPublication({
        botId: 'external-1',
        audience: 'bot',
        isExternalBot: true,
        config: { scope: 'restricted', organizationPaths: [] },
      }),
    ).rejects.toThrow('组织范围缺少部门名称，无法保存');
    expect(getCollaborationBot).not.toHaveBeenCalled();
    expect(patchCollaborationBot).not.toHaveBeenCalled();
  });

  it('GET friend_ext 失败或 PATCH 失败都不产出成功结果', async () => {
    const { adapter, listMyBots, getCollaborationBot, patchCollaborationBot } = createExternalAdapter();
    listMyBots.mockResolvedValue(mockMineWithExternalBot({ dto: createExternalBotDto({ visibility: 'public' }) }));
    await adapter.loadOverview('900004', undefined, externalScope);

    getCollaborationBot.mockRejectedValue(new Error('Bot 详情接口返回异常'));
    await expect(
      adapter.submitPublication({
        botId: 'external-1',
        audience: 'bot',
        isExternalBot: true,
        config: { scope: 'restricted', organizationPaths: [['蚂蚁集团-CRO线-大安全技术部']] },
        deptEntries: [{ deptNo: 'A4195', deptName: '蚂蚁集团-CRO线-大安全技术部' }],
      }),
    ).rejects.toThrow('Bot 详情接口返回异常');
    expect(patchCollaborationBot).not.toHaveBeenCalled();

    getCollaborationBot.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: createExternalBotDto({ visibility: 'protected' }),
    });
    patchCollaborationBot.mockRejectedValue(new Error('Bot 更新接口返回异常'));
    await expect(
      adapter.submitPublication({
        botId: 'external-1',
        audience: 'bot',
        isExternalBot: true,
        config: { scope: 'restricted', organizationPaths: [['蚂蚁集团-CRO线-大安全技术部']] },
        deptEntries: [{ deptNo: 'A4195', deptName: '蚂蚁集团-CRO线-大安全技术部' }],
      }),
    ).rejects.toThrow('Bot 更新接口返回异常');
    expect(patchCollaborationBot).toHaveBeenCalledTimes(1);
  });

  it('外部命令仅支持对 Bot 受众，用户受众直接拒绝以免误改 Bot 可见性字段', async () => {
    const { adapter, listMyBots, getCollaborationBot, patchCollaborationBot } = createExternalAdapter();
    listMyBots.mockResolvedValue(mockMineWithExternalBot({ dto: createExternalBotDto() }));
    await adapter.loadOverview('900004', undefined, externalScope);

    await expect(
      adapter.submitPublication({
        botId: 'external-1',
        audience: 'user',
        isExternalBot: true,
        config: { scope: 'none', organizationPaths: [] },
      }),
    ).rejects.toThrow('外部 Bot 仅支持配置对 Bot 可见性');
    expect(getCollaborationBot).not.toHaveBeenCalled();
    expect(patchCollaborationBot).not.toHaveBeenCalled();
  });

  it('外部限定组织直改成功后刷新快照：随后的好友审批整体替换保留新名称与未知键', async () => {
    const { adapter, listMyBots, getCollaborationBot, patchCollaborationBot } = createExternalAdapter();
    const initialFriendExt = { custom_flags: { nested: { keep: true } }, view_scope_agent_friend_deps: ['旧范围'] };
    listMyBots.mockResolvedValue(
      mockMineWithExternalBot({
        dto: createExternalBotDto({ visibility: 'protected', friend_ext: initialFriendExt }),
      }),
    );
    getCollaborationBot.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: createExternalBotDto({ visibility: 'protected', friend_ext: initialFriendExt }),
    });
    patchCollaborationBot
      .mockResolvedValueOnce({
        code: 20000,
        message: 'OK',
        data: createExternalBotDto({
          visibility: 'protected',
          friend_ext: { custom_flags: { nested: { keep: true } }, view_scope_agent_friend_deps: ['蚂蚁集团-研究院'] },
        }),
      })
      .mockResolvedValueOnce({
        code: 20000,
        message: 'OK',
        data: createExternalBotDto({
          visibility: 'protected',
          friend_ext: {
            custom_flags: { nested: { keep: true } },
            view_scope_agent_friend_deps: ['蚂蚁集团-研究院'],
            no_check_scope_friend_deps: [],
          },
          friend_check_in_strategy: 'OPEN',
        }),
      });
    await adapter.loadOverview('900004', undefined, externalScope);

    await adapter.submitPublication({
      botId: 'external-1',
      audience: 'bot',
      isExternalBot: true,
      config: { scope: 'restricted', organizationPaths: [['蚂蚁集团-研究院']] },
      deptEntries: [{ deptNo: 'A5000', deptName: '蚂蚁集团-研究院' }],
    });
    await adapter.updateFriendApproval({
      botId: 'external-1',
      config: { mode: 'none', exemptOrganizationPaths: [] },
    });

    // 第二次 PATCH（好友审批整体替换 friend_ext）必须基于直改后的最新快照：
    // 保留刚保存的可见性范围名称，同时不丢未知嵌套键。
    const friendApprovalPatch = patchCollaborationBot.mock.calls.at(-1);
    expect(friendApprovalPatch?.[0]).toBe('external-1');
    expect(friendApprovalPatch?.[1]).toEqual({
      friend_ext: {
        custom_flags: { nested: { keep: true } },
        view_scope_agent_friend_deps: ['蚂蚁集团-研究院'],
        no_check_scope_friend_deps: [],
      },
      friend_check_in_strategy: 'OPEN',
    });
  });

  it('外部直改 PATCH 失败不推进快照：好友审批仍基于加载时的 friend_ext 合并', async () => {
    const { adapter, listMyBots, getCollaborationBot, patchCollaborationBot } = createExternalAdapter();
    const initialFriendExt = { custom_flags: { nested: { keep: true } } };
    listMyBots.mockResolvedValue(
      mockMineWithExternalBot({
        dto: createExternalBotDto({ visibility: 'public', friend_ext: initialFriendExt }),
      }),
    );
    getCollaborationBot.mockResolvedValue({
      code: 20000,
      message: 'OK',
      data: createExternalBotDto({ visibility: 'public', friend_ext: initialFriendExt }),
    });
    patchCollaborationBot
      .mockRejectedValueOnce(new Error('Bot 更新接口返回异常'))
      .mockResolvedValueOnce({
        code: 20000,
        message: 'OK',
        data: createExternalBotDto({
          visibility: 'public',
          friend_ext: { custom_flags: { nested: { keep: true } }, no_check_scope_friend_deps: [] },
          friend_check_in_strategy: 'OPEN',
        }),
      });
    await adapter.loadOverview('900004', undefined, externalScope);

    await expect(
      adapter.submitPublication({
        botId: 'external-1',
        audience: 'bot',
        isExternalBot: true,
        config: { scope: 'restricted', organizationPaths: [['蚂蚁集团-研究院']] },
        deptEntries: [{ deptNo: 'A5000', deptName: '蚂蚁集团-研究院' }],
      }),
    ).rejects.toThrow('Bot 更新接口返回异常');
    await adapter.updateFriendApproval({
      botId: 'external-1',
      config: { mode: 'none', exemptOrganizationPaths: [] },
    });

    // 保存失败未落库：快照不得带入本次提交的范围名称，好友审批仍从加载时 friend_ext 合并。
    const friendApprovalPatch = patchCollaborationBot.mock.calls.at(-1);
    expect(friendApprovalPatch?.[1]).toEqual({
      friend_ext: {
        custom_flags: { nested: { keep: true } },
        no_check_scope_friend_deps: [],
      },
      friend_check_in_strategy: 'OPEN',
    });
  });
});

describe('collaborationPrivacyRuntimeAdapter 外部 Bot 加载展示', () => {
  it('public 即使响应保留陈旧部门范围也展示全部可见，审批类字段不生成 pending/链接', async () => {
    const { adapter, listMyBots, listOrgDepts } = createExternalAdapter();
    const staleDto = createExternalBotDto({
      visibility: 'public',
      friend_ext: {
        view_scope_agent_friend_deps: ['蚂蚁集团-CRO线-大安全技术部'],
        public_agent_approval: { status: 'CREATED', puid: 'PU-1', visibility: 'public', approval_url: 'https://example.com/inspection' },
      },
    });
    listMyBots.mockResolvedValue(mockMineWithExternalBot({ dto: staleDto }));

    const overview = await adapter.loadOverview('900004', undefined, externalScope);

    expect(overview.bots[0].publication.bot).toEqual({ scope: 'all', organizationPaths: [] });
    expect(overview.bots[0].pendingPublications).toEqual({});
    // 部门名称字符串绝不当作部门编码发起回显查询。
    expect(listOrgDepts).not.toHaveBeenCalled();

    // 同一份 DTO 不带 external 标记仍走既有 TC 映射（public + 非空范围 = restricted，pending 照常生成）。
    const tcOverview = await adapter.loadOverview('900004', undefined, { target: 'activeBot', botId: 'external-1' });
    expect(tcOverview.bots[0].publication.bot.scope).toBe('restricted');
    expect(tcOverview.bots[0].pendingPublications.bot).toBeDefined();
  });

  it('protected + 名称字符串按原名称回显为限定组织可申请，可直接回填再编辑', async () => {
    const { adapter, listMyBots, listOrgDepts } = createExternalAdapter();
    listMyBots.mockResolvedValue(
      mockMineWithExternalBot({
        dto: createExternalBotDto({
          visibility: 'protected',
          friend_ext: { view_scope_agent_friend_deps: ['蚂蚁集团-CRO线-大安全技术部'] },
        }),
      }),
    );

    const overview = await adapter.loadOverview('900004', undefined, externalScope);

    expect(overview.bots[0].publication.bot).toEqual({
      scope: 'restricted',
      organizationPaths: [['蚂蚁集团-CRO线-大安全技术部']],
    });
    expect(overview.bots[0].pendingPublications).toEqual({});
    expect(listOrgDepts).not.toHaveBeenCalled();
  });
});
