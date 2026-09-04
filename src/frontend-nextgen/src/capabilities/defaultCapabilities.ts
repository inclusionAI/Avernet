import { normalizeOpenApiUserId, resolveOpenApiUserId } from '@/domain/userIdentity';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { AvernetMarkLogo, AvernetWordmarkLogo } from './brandLogos';
import type {
  AgentCodingInternalResources,
  AdminSections,
  AppCapabilities,
  BotEngineOption,
  CapabilityResult,
  HumanIdentity,
  LoginStrategy,
  MetricsDashboardSpec,
  PersonalSpaceInitOptions,
  ProductBrand,
  ReleaseNotesCapability,
  ShellVisibility,
  UserSearchCapability,
} from './types';

function unsupported<T>(value: T, reason: string): CapabilityResult<T> {
  return { status: 'unsupported', value, reason };
}

export const defaultCapabilities: AppCapabilities = {
  // Open Core 默认不暴露内部帮助链接，避免内部 URL 泄漏。
  getHelpLinks: () => ({ status: 'available', value: [] }),
  openExternal: () => unsupported(null, '当前运行环境暂不支持打开外部链接'),
  // Open Core 默认路由重定向：当前无收敛项——管理后台入口已开放（getShellVisibility.adminEntry=true），
  // /admin 可达，不再重定向至 /manage。返回 null。internal overlay 仍显式 override 为 null
  // 作为防御性回归护栏（见 src/extensions/internal.ts），防未来 Open 默认重引入 /admin 重定向经 spread 误伤 internal。
  getRuntimeRouteRedirect: () => ({ status: 'available', value: null }),
  getBotHealthCapability: () => ({
    status: 'available',
    value: {
      dimensions: [
        'configuration',
        'taskUnderstanding',
        'planningExecution',
        'capabilityInvocation',
        'contextLearning',
        'taskDelivery',
      ],
      showRadar: true,
      showLogDetails: false,
      showRawSnapshot: false,
    },
  }),
  getCurrentOpenApiUserId: (context) => {
    const normalized = normalizeOpenApiUserId(context?.activeIdentityId);
    if (!normalized) return unsupported(undefined, '缺少当前用户身份');
    return { status: 'available', value: normalized };
  },
  // Open Core（oauth-provider 策略）：优先用 /auth/user 解析的外部登录用户（externalAuthStore.user）；
  // 未登录回退 listMyBots me（listMyBots human[0]）。不读 cookie/__TERN__/内部 URL（internal overlay 覆盖）。
  getHumanIdentity: () => {
    const oauthUser = useExternalAuthStore.getState().user;
    if (oauthUser) {
      return {
        status: 'available',
        value: {
          userId: oauthUser.userId,
          displayName: oauthUser.displayName,
          avatarUrl: oauthUser.avatarUrl,
          online: true,
        },
      };
    }
    const { identities } = useWorkspaceStore.getState();
    const me = identities.find((i) => i.kind === 'user') ?? identities[0] ?? null;
    if (!me) return { status: 'available', value: null };
    const userId = resolveOpenApiUserId(me.id).trim();
    if (!userId) return { status: 'available', value: null };
    const identity: HumanIdentity = {
      userId,
      displayName: me.displayName,
      avatarUrl: me.avatarUrl,
      online: me.online,
    };
    return { status: 'available', value: identity };
  },
  // Open Core 无雨燕配置数据源 → 版本发布说明不支持（菜单不渲染该项）。
  getReleaseNotesCapability: (): CapabilityResult<ReleaseNotesCapability | null> => ({
    status: 'available',
    value: null,
  }),
  // Open Core 无员工目录数据源 → 员工搜索不支持（添加成员弹窗降级为手填工号）。
  getUserSearchCapability: (): CapabilityResult<UserSearchCapability | null> => ({
    status: 'available',
    value: null,
  }),
  // Open Core 无内部 AntMonitor 监控大盘数据源 → 平台指标回退静态占位 4 区（PlatformMetricsPanel mock）。
  getMetricsDashboard: (): CapabilityResult<MetricsDashboardSpec> => ({
    status: 'available',
    value: { url: null },
  }),
  // Open Core 不暴露内部应用 Coding 帮助视频；internal overlay 注入真实资源。
  getAppCodingYuqueTokenGuideVideoUrl: (): CapabilityResult<string | null> => ({
    status: 'available',
    value: null,
  }),
  // Open Core 不依赖内部 CodeFuse 模型目录；内部 overlay 注入老版模型接口。
  getCodefuseModelsUrl: (): CapabilityResult<string | null> => ({
    status: 'available',
    value: null,
  }),
  getAgentCodingInternalResources: (): CapabilityResult<AgentCodingInternalResources> => ({
    status: 'available',
    value: {
      templateFactoryUrl: null,
      imageManualUrl: null,
      imageBuildUrl: null,
      workflowRepositoryBaseUrl: null,
      codefuseTokenUrl: null,
      antCodeProjectsApiUrl: null,
      antCodeProjectBaseUrl: null,
    },
  }),
  // Open Core 无内网 antwork 照片服务数据源 → 成员头像返回 null，成员行 UI 回退首字母占位。
  // 真实 URL 拼接只在 internal overlay（src/extensions/internal.ts，.internal-paths 剥离）。
  getMemberAvatarUrl: (): CapabilityResult<string | null> => ({ status: 'available', value: null }),
  // Open Core 默认走外部 OAuth provider 登录（开源部署 = 外部用户，无 ACE）；internal overlay 覆盖为 'ace-gateway'（员工）。
  getLoginStrategy: (): CapabilityResult<LoginStrategy> => ({ status: 'available', value: 'oauth-provider' }),
  // Open Core 默认不暴露内部侧栏导航项（能力工坊/能力市场），符合 open-core-export-plan §5.2
  // 「导航中的内部入口」按开源模式分隔的强约束。internal overlay 经 extensions/internal.ts 覆盖注入。
  getInternalNavigationItems: () => ({ status: 'available', value: [] }),
  // Open Core 默认不收录内部 route meta（/capability-workshop/*、/market/* 已从 routeMetaList 剥离）。
  getInternalRouteMetas: () => ({ status: 'available', value: [] }),
  // Open Core 引擎可选清单：OpenClaw + 原生 Claude Code 直建入口（阿里云部署依赖原生 claude_code
  // 引擎展示与创建，不可删除）。AgentCoding（管理 CC 模板创建）仍仅由 internal overlay 注入。
  // 引擎领域映射规则属后端契约事实，保留在领域层全量，不随本清单收窄。
  getBotEngineOptions: (): CapabilityResult<BotEngineOption[]> => ({
    status: 'available',
    value: [
      { value: 'openclaw', label: 'OpenClaw' },
      { value: 'claude_code', label: 'Claudecode引擎-原生' },
    ],
  }),
  // Open Core 品牌：Avernet（横版 wordmark 用于页头；方版 mark 备用于登录/空态方形场景）。
  getProductBrand: (): CapabilityResult<ProductBrand> => ({
    status: 'available',
    value: { name: 'Avernet', Logo: AvernetWordmarkLogo, loginWordmark: AvernetMarkLogo },
  }),
  // Open Core（阿里云部署）初始化个人空间需在 body 携带 skipSC:true；internal overlay 覆盖为 {}
  // （内部后端不消费该字段，保持历史空 body 契约）。
  getPersonalSpaceInitOptions: (): CapabilityResult<PersonalSpaceInitOptions> => ({
    status: 'available',
    value: { skipSC: true },
  }),
  // Open Core（阿里云部署）壳层可见性：展示【管理后台】导航项与页头通知中心
  // （adminEntry/notificationBell=true），不展示侧栏空间切换器（spaceSwitcher=false）；
  // 空间数据链路（initSpaceContext / 默认个人空间）与此开关无关，不受本默认值影响。
  // internal overlay 覆盖为三项全 true（extensions/internal.ts），内部形态零变化。
  getShellVisibility: (): CapabilityResult<ShellVisibility> => ({
    status: 'available',
    value: { adminEntry: true, spaceSwitcher: false, notificationBell: true },
  }),
  // Open Core（阿里云部署）管理后台页内分区：隐藏【空间管理】Tab、仅保留【工单中心】
  // （管理后台入口已开放，空间管理收敛在页内完成；空间数据链路不受影响）。
  // internal overlay 覆盖为 { spaces:true, workOrders:true }（extensions/internal.ts），内部形态两 Tab 均在。
  getAdminSections: (): CapabilityResult<AdminSections> => ({
    status: 'available',
    value: { spaces: false, workOrders: true },
  }),
};
