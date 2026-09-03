import { defaultCapabilities } from '@/capabilities/defaultCapabilities';
import { useExternalAuthStore } from '@/stores/externalAuthStore';

describe('Open Core default capabilities', () => {
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

  test('getShellVisibility 默认三项全 false（Open Core 隐藏管理后台入口/空间切换器/通知中心）', () => {
    const r = defaultCapabilities.getShellVisibility();
    expect(r.status).toBe('available');
    expect(r.value).toEqual({ adminEntry: false, spaceSwitcher: false, notificationBell: false });
  });

  test('getRuntimeRouteRedirect 对 /admin 系直访重定向 /manage（open 形态基线路由不可达）', () => {
    expect(defaultCapabilities.getRuntimeRouteRedirect({ pathname: '/admin' }).value).toBe('/manage');
    expect(defaultCapabilities.getRuntimeRouteRedirect({ pathname: '/admin/spaces' }).value).toBe('/manage');
    expect(defaultCapabilities.getRuntimeRouteRedirect({ pathname: '/admin/work-orders' }).value).toBe('/manage');
    expect(defaultCapabilities.getRuntimeRouteRedirect({ pathname: '/admin' }).status).toBe('available');
  });

  test('getRuntimeRouteRedirect 非 /admin 路径维持 null（含前缀误伤防护）', () => {
    for (const pathname of ['/workspace', '/bot-workshop', '/manage', '/administrator', '/adminx', '/']) {
      expect(defaultCapabilities.getRuntimeRouteRedirect({ pathname }).value).toBeNull();
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
});
