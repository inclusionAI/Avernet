import { defaultCapabilities } from '@/capabilities/defaultCapabilities';
import { useExternalAuthStore } from '@/stores/externalAuthStore';

describe('Open Core default capabilities', () => {
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
    expect(r.value.map((o) => o.value)).toEqual(['openclaw', 'claude_code']);
    expect(r.value.map((o) => o.label)).toEqual(['OpenClaw', 'Claudecode引擎-原生']);
  });

  test('getBotSkillPickerSources 默认仅我的 Skill（Open Core / 阿里云隐藏内部来源）', () => {
    expect(defaultCapabilities.getBotSkillPickerSources()).toEqual({ status: 'available', value: ['mine'] });
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

  test('getShellVisibility 默认 adminEntry/notificationBell=true、spaceSwitcher=false（Open Core 展示管理后台与通知中心，不展示空间切换器）', () => {
    const r = defaultCapabilities.getShellVisibility();
    expect(r.status).toBe('available');
    expect(r.value).toEqual({ adminEntry: true, spaceSwitcher: false, notificationBell: true });
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

  test('getTaskClaimGrantStrategy 默认 skip（Open Core api-key 直发,secbaas grant 短路 no-op）', () => {
    const r = defaultCapabilities.getTaskClaimGrantStrategy();
    expect(r.status).toBe('available');
    expect(r.value).toBe('skip');
  });
});
