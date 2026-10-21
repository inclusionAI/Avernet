/** @jest-environment jsdom */
import { defaultCapabilities } from '@/capabilities/defaultCapabilities';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { history as mockHistory } from '@umijs/max';
import { Bot, Terminal } from 'lucide-react';

describe('Open Core default capabilities', () => {
  test('additional onboarding panels default to empty in Open Core', () => {
    expect(defaultCapabilities.getAdditionalBotRegistrationPanels()).toEqual({
      status: 'available',
      value: [],
    });
  });

  test('健康检查默认只开放配置健康度，不展示内部多维视图', () => {
    const r = defaultCapabilities.getBotHealthCapability();
    expect(r.status).toBe('available');
    expect(r.value).toEqual({
      dimensions: ['configuration'],
      showRadar: false,
      showLogDetails: false,
      showRawSnapshot: false,
    });
  });

  test('getHelpLinks 默认空数组（Open Core 无内网 URL）', () => {
    const r = defaultCapabilities.getHelpLinks();
    expect(r.status).toBe('available');
    expect(r.value).toEqual([]);
  });

  test('getOpenSourceExperienceNotice 返回 Open Core v1 提示配置', () => {
    expect(defaultCapabilities.getOpenSourceExperienceNotice()).toEqual({
      status: 'available',
      value: {
        version: 'open-source-experience-v1',
        message: '本环境仅供开源版本进行功能体验，不提供正式生产服务。请不要上传敏感数据。',
        acknowledgeLabel: '我已知悉',
      },
    });
  });

  test('getReleaseNotesCapability 默认 null（Open Core 无雨燕数据源）', () => {
    const r = defaultCapabilities.getReleaseNotesCapability();
    expect(r.status).toBe('available');
    expect(r.value).toBeNull();
  });

  test('getMetricsDashboard 默认 url=null（Open Core 无内部 AntMonitor 数据源，回退静态 4 区）', () => {
    const r = defaultCapabilities.getMetricsDashboard();
    expect(r.status).toBe('available');
    expect(r.value).toEqual({ url: null });
  });

  test('getAgentCodingInternalResources 默认全部为 null（Open Core 不泄漏内部资源地址）', () => {
    const r = defaultCapabilities.getAgentCodingInternalResources();
    expect(r.status).toBe('available');
    expect(Object.values(r.value)).toEqual(expect.arrayContaining([null]));
    expect(Object.values(r.value).every((value) => value === null)).toBe(true);
  });

  test('getMemberAvatarUrl 默认 null（Open Core 无内网 antwork 照片服务，成员行回退首字母占位）', () => {
    const r = defaultCapabilities.getMemberAvatarUrl('208800');
    expect(r.status).toBe('available');
    expect(r.value).toBeNull();
  });

  test('占位身份 me 不能作为 OpenAPI user_id', () => {
    expect(defaultCapabilities.getCurrentOpenApiUserId({ activeIdentityId: 'me' })).toMatchObject({
      status: 'unsupported',
      value: undefined,
    });
  });

  test('getLoginStrategy 默认 oauth-provider（Open Core = 外部登录）', () => {
    const r = defaultCapabilities.getLoginStrategy();
    expect(r.status).toBe('available');
    expect(r.value).toBe('oauth-provider');
  });

  test('getInternalNavigationItems 默认空数组（Open Core 不暴露能力工坊/能力市场内部入口）', () => {
    const r = defaultCapabilities.getInternalNavigationItems();
    expect(r.status).toBe('available');
    expect(r.value).toEqual([]);
  });

  test('getInternalRouteMetas 默认空数组（Open Core 不收录内部 route meta）', () => {
    const r = defaultCapabilities.getInternalRouteMetas();
    expect(r.status).toBe('available');
    expect(r.value).toEqual([]);
  });

  test('getBotEngineOptions 默认 OpenClaw + Claudecode引擎-原生（阿里云部署依赖原生 CC 直建入口）', () => {
    const r = defaultCapabilities.getBotEngineOptions();
    expect(r.status).toBe('available');
    expect(r.value).toEqual([
      expect.objectContaining({
        value: 'openclaw',
        label: 'OpenClaw',
        description: '通用 AI 对话助手，适用于日常工作与知识问答',
        icon: Bot,
      }),
      expect.objectContaining({
        value: 'claude_code',
        label: 'Claudecode引擎-原生',
        cardLabel: 'Claude Code',
        description: '原生 Claude Code 引擎',
        icon: Terminal,
      }),
    ]);
    expect(r.value.map((option) => option.tag)).toEqual([undefined, undefined]);
    expect(r.value.map((option) => option.createPanel)).toEqual([undefined, undefined]);
  });

  test('getBotSkillPickerSources 默认仅我的 Skill（Open Core / 阿里云隐藏内部来源）', () => {
    expect(defaultCapabilities.getBotSkillPickerSources()).toEqual({ status: 'available', value: ['mine'] });
  });

  test('Open Core 不提供内部市场与工坊详情地址', () => {
    expect(defaultCapabilities.getBotSkillPickerDetailUrl({ id: '42', name: 'Skill', active: false })).toEqual({
      status: 'unsupported',
      value: null,
    });
  });

  test('Open Core / 阿里云隐藏 MCP 添加能力', () => {
    expect(defaultCapabilities.getBotMcpPickerEnabled()).toEqual({ status: 'available', value: false });
  });

  test('getProductBrand 默认 Avernet（横版 wordmark + 方版 mark 视觉组件）', () => {
    const r = defaultCapabilities.getProductBrand();
    expect(r.status).toBe('available');
    expect(r.value.name).toBe('Avernet');
    expect(typeof r.value.Logo).toBe('function');
    expect(typeof r.value.loginWordmark).toBe('function');
  });

  test('getPersonalSpaceInitOptions 默认 skipSC:true（阿里云部署 body 契约）', () => {
    const r = defaultCapabilities.getPersonalSpaceInitOptions();
    expect(r.status).toBe('available');
    expect(r.value).toEqual({ skipSC: true });
  });

  test('getBotRegistrationEnabled 默认 true（Open Core 展示 Bot 自助接入入口）', () => {
    const r = defaultCapabilities.getBotRegistrationEnabled();
    expect(r.status).toBe('available');
    expect(r.value).toBe(true);
  });

  test('getGroupAdvancedConfigEnabled 默认 false（Open Core 不展示群高级配置）', () => {
    const r = defaultCapabilities.getGroupAdvancedConfigEnabled();
    expect(r.status).toBe('available');
    expect(r.value).toBe(false);
  });

  test('getUserProfilePresentation 默认使用认证用户名称并隐藏部门（Open Core）', () => {
    expect(defaultCapabilities.getUserProfilePresentation()).toEqual({
      status: 'available',
      value: { preferAuthenticatedUserProfile: true, showDepartment: false },
    });
  });

  test('getRestrictedPublicationScopeEnabled 默认 false（Open Core 隐藏限定组织可申请）', () => {
    expect(defaultCapabilities.getRestrictedPublicationScopeEnabled()).toEqual({ status: 'available', value: false });
  });

  test('getPartialFriendApprovalEnabled 默认 false（Open Core 隐藏部分组织免审批）', () => {
    expect(defaultCapabilities.getPartialFriendApprovalEnabled()).toEqual({ status: 'available', value: false });
  });

  test('getShellVisibility 默认 notificationBell=true、spaceSwitcher=false（Open Core 展示通知中心，不展示空间切换器；adminEntry 已随管理后台导航项退役移除）', () => {
    const r = defaultCapabilities.getShellVisibility();
    expect(r.status).toBe('available');
    expect(r.value).toEqual({ spaceSwitcher: false, notificationBell: true });
  });

  test('getAdminSections 默认 spaces=false/workOrders=true（Open Core 隐藏空间管理 Tab，仅留工单中心）', () => {
    const r = defaultCapabilities.getAdminSections();
    expect(r.status).toBe('available');
    expect(r.value).toEqual({ spaces: false, workOrders: true });
  });

  test('getRuntimeRouteRedirect 恒返回 null（管理后台入口已开放，/admin 可达，不再重定向 /manage）', () => {
    const cases = [
      '/admin',
      '/admin/spaces',
      '/admin/work-orders',
      '/workspace',
      '/bot-workshop',
      '/manage',
      '/administrator',
      '/adminx',
      '/',
    ];
    for (const pathname of cases) {
      const r = defaultCapabilities.getRuntimeRouteRedirect({ pathname });
      expect(r.status).toBe('available');
      expect(r.value).toBeNull();
    }
  });

  test('getHumanIdentity：externalAuthStore.user 已登录 → 返回 oauth 外部身份（oauth-provider 策略）', () => {
    useExternalAuthStore.getState().reset();
    useExternalAuthStore
      .getState()
      .setAuthenticated({ userId: 'ext-1', displayName: 'Alice', provider: 'alipay', avatarUrl: 'https://a' });
    const r = defaultCapabilities.getHumanIdentity();
    expect(r.status).toBe('available');
    expect(r.value).toMatchObject({ userId: 'ext-1', displayName: 'Alice', avatarUrl: 'https://a', online: true });
    useExternalAuthStore.getState().reset();
  });

  test('getHumanIdentity：externalAuthStore 未登录 → 回退 null（Open Core me 未就绪）', () => {
    useExternalAuthStore.getState().reset();
    const r = defaultCapabilities.getHumanIdentity();
    expect(r.value).toBeNull();
    useExternalAuthStore.getState().reset();
  });

  test('getBbsApiBase 默认 /openapi/v1/collaboration/tasks/bbs/topics（BBS 已发布公开面）', () => {
    const r = defaultCapabilities.getBbsApiBase();
    expect(r.status).toBe('available');
    expect(r.value).toBe('/openapi/v1/collaboration/tasks/bbs/topics');
  });

  test('getTaskClaimGrantStrategy 默认 skip（Open Core api-key 直发,secbaas grant 短路 no-op）', () => {
    const r = defaultCapabilities.getTaskClaimGrantStrategy();
    expect(r.status).toBe('available');
    expect(r.value).toBe('skip');
  });
});

// ── 任务副屏 V2 会话联动(P4):会话宿主内只替换左侧内容，宿主外才走路由 ──
jest.mock('@umijs/max', () => ({ history: { push: jest.fn(), replace: jest.fn() } }));

describe('defaultCapabilities.navigateToConversation', () => {
  const pushMock = mockHistory.push as unknown as jest.Mock;
  const replaceMock = (mockHistory as unknown as { replace?: jest.Mock }).replace as jest.Mock | undefined;

  const setPath = (pathname: string) => {
    window.history.pushState({}, '', pathname);
  };

  beforeEach(() => {
    useTaskSidePanelStore.getState().resetAll();
    pushMock.mockClear();
    replaceMock?.mockClear();
  });

  it.each(['/workspace/chat', '/workspace/collaboration'])(
    '%s 内点群节点 → 左侧联动群会话，TaskPanel 宿主路由不变',
    (pathname) => {
      setPath(pathname);
      const result = defaultCapabilities.navigateToConversation({
        sessionId: 'bcs_grp_902:round:2',
        kind: 'group',
        groupId: 'g-902',
      });

      expect(result.status).toBe('available');
      expect(useTaskSidePanelStore.getState().linkedSession).toEqual({
        sessionId: 'bcs_grp_902:round:2',
        kind: 'group',
        groupId: 'g-902',
        botId: null,
      });
      expect(pushMock).not.toHaveBeenCalled();
      expect(replaceMock).not.toHaveBeenCalled();
    },
  );

  it.each(['/workspace/chat', '/workspace/collaboration'])(
    '%s 内点 Bot 节点 → 左侧联动 Bot 会话，TaskPanel 宿主路由不变',
    (pathname) => {
      setPath(pathname);
      const result = defaultCapabilities.navigateToConversation({
        sessionId: 'agent:main:session:s-1',
        kind: 'bot',
        botId: 'bot-9',
      });

      expect(result.status).toBe('available');
      expect(useTaskSidePanelStore.getState().linkedSession).toEqual({
        sessionId: 'agent:main:session:s-1',
        kind: 'bot',
        groupId: null,
        botId: 'bot-9',
      });
      expect(pushMock).not.toHaveBeenCalled();
      expect(replaceMock).not.toHaveBeenCalled();
    },
  );

  it('kind=unknown 但 sessionId 为 bcs_grp_ 形态 → 按群会话联动', () => {
    setPath('/workspace/chat');
    defaultCapabilities.navigateToConversation({
      sessionId: 'bcs_grp_902:round:2',
      kind: 'unknown',
    });

    expect(useTaskSidePanelStore.getState().linkedSession).toEqual({
      sessionId: 'bcs_grp_902:round:2',
      kind: 'group',
      groupId: null,
      botId: null,
    });
    expect(pushMock).not.toHaveBeenCalled();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it('会话宿主之外点群节点 → 路由进入协作群会话页', () => {
    setPath('/work/my-task');
    const result = defaultCapabilities.navigateToConversation({
      sessionId: 'bcs_grp_902:round:2',
      kind: 'group',
      groupId: 'g-902',
    });

    expect(result.status).toBe('available');
    const url = pushMock.mock.calls[0][0] as string;
    expect(url.startsWith('/workspace/collaboration?')).toBe(true);
    expect(url).toContain('session=');
    expect(url).toContain('group=');
    expect(useTaskSidePanelStore.getState().linkedSession).toBeNull();
  });

  it('会话宿主之外点 Bot 节点 → 路由进入单聊会话页', () => {
    setPath('/work/my-task');
    defaultCapabilities.navigateToConversation({
      sessionId: 'agent:main:session:s-1',
      kind: 'bot',
      botId: 'bot-9',
    });

    const url = pushMock.mock.calls[0][0] as string;
    expect(url.startsWith('/workspace/chat?')).toBe(true);
    expect(url).toContain('bot=bot-9');
    expect(url).toContain('session=');
    expect(useTaskSidePanelStore.getState().linkedSession).toBeNull();
  });

  it('sessionId 缺失 → unsupported，不触发联动或路由', () => {
    const result = defaultCapabilities.navigateToConversation({ sessionId: '', kind: 'unknown' });
    expect(result.status).toBe('unsupported');
    expect(useTaskSidePanelStore.getState().linkedSession).toBeNull();
    expect(pushMock).not.toHaveBeenCalled();
    expect(replaceMock).not.toHaveBeenCalled();
  });
});
