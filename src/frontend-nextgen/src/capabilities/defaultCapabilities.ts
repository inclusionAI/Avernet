import { resolveOpenApiUserId } from '@/domain/userIdentity';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type {
  AppCapabilities,
  CapabilityResult,
  HumanIdentity,
  MetricsDashboardSpec,
  ReleaseNotesCapability,
  UserSearchCapability,
} from './types';

function unsupported<T>(value: T, reason: string): CapabilityResult<T> {
  return { status: 'unsupported', value, reason };
}

export const defaultCapabilities: AppCapabilities = {
  // Open Core 默认不暴露内部帮助链接，避免内部 URL 泄漏。
  getHelpLinks: () => ({ status: 'available', value: [] }),
  openExternal: () => unsupported(null, '当前运行环境暂不支持打开外部链接'),
  // Open Core 默认不做内部域名路由跳转，内部规则由 capability 注入。
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
    const normalized = context?.activeIdentityId ? resolveOpenApiUserId(context.activeIdentityId).trim() : '';
    if (!normalized) return unsupported(undefined, '缺少当前用户身份');
    return { status: 'available', value: normalized };
  },
  // Open Core 默认：从 workspaceStore.identities 取 me（listMyBots human[0]）。
  // 不读 cookie/__TERN__/内部 URL；未就绪返回 null（useHumanIdentity 会触发 loadIdentities）。
  getHumanIdentity: () => {
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
};
