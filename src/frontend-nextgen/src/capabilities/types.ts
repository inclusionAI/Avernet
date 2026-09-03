import type { BotHealthCapability } from '@/domain/botHealthCheck';
import type { NavigationItem } from '@/shell/navigation';
import type { RouteMeta } from '@/shell/routeMeta';
import type { ComponentType } from 'react';

export type CapabilityStatus = 'available' | 'unsupported';

export interface CapabilityResult<T> {
  status: CapabilityStatus;
  value: T;
  reason?: string;
}

export interface RuntimeRouteContext {
  pathname: string;
}

export interface CurrentUserContext {
  activeIdentityId?: string | null;
}

/**
 * 当前登录 human 用户身份。由 `getHumanIdentity` 契约解析，AccountBadge 等只读消费。
 * - userId：OpenAPI user_id（去 human_ 前缀，对齐 resolveOpenApiUserId）
 * - displayName：花名/昵称（用于展示）
 * - avatarUrl：头像 URL；缺省时 UI 走渐变圆兜底
 * - online：在线状态快照（不做实时推送）
 */
export interface HumanIdentity {
  userId: string;
  displayName: string;
  avatarUrl?: string;
  online: boolean;
}

/**
 * 帮助菜单外链项。openExternal capability 负责打开 href（内部 overlay 用平台 scheme/新开页）。
 * - group=manual：用户手册；group=robot：答疑机器人；group=product：产品获取（TUI/移动端/桌面端）
 * - icon：菜单图标语义键，HelpMenu 内部映射到 lucide 图标（避免在此层依赖具体图标库）
 * Open Core 默认 []（defaultCapabilities），内网 URL 经 internal overlay 注入，不进 Open Core 产物。
 */
export interface HelpLink {
  label: string;
  href: string;
  description?: string;
  icon?: 'manual' | 'robot' | 'tui' | 'mobile' | 'desktop';
  group: 'manual' | 'robot' | 'product';
}

/**
 * 版本发布说明数据（用户须知 + 新版本内容）。由 ReleaseNotesCapability.load 异步拉取，
 * 数据源为内部雨燕配置（teamclaw-release-note_*）。富文本 HTML 来自受信运营平台，UI 用
 * dangerouslySetInnerHTML 渲染。Open Core 无此数据源 → capability 返回 null（菜单不展示该项）。
 */
export interface ReleaseNotesData {
  version?: string;
  date?: string;
  userReadmeHtml?: string;
  releaseNoteHtml?: string;
}

/**
 * 版本发布说明能力。Open Core 返回 null（不支持）；internal overlay 提供实现：
 * - load()：拉雨燕配置，失败/空返回 null（UI 降级「暂无内容」）
 * - markSeen(date)：用户打开 Modal 后写入 localStorage 记录已读日期，清除菜单红点
 */
export interface ReleaseNotesCapability {
  load(): Promise<ReleaseNotesData | null>;
  markSeen(date: string): void;
  /** 读取用户已读版本发布说明的日期记录；null=未读（菜单显示红点）。 */
  getSeenDate(): string | null;
}

/**
 * 员工目录搜索结果（Open Core 领域模型，已剥离 Antbu 原始字段，通过 DTO-leak 门禁）。
 * 由 getUserSearchCapability 在内部 overlay 经 antbuservice 搜索后映射得到。
 * Open Core 默认 null（无员工目录数据源）。userId=工号(staffNo)，作为 addSpaceMember
 * 的 member_user_id；displayName=花名/真名，用于头像与标题。
 */
export interface SearchedUser {
  /** 工号(=staffNo)，空间成员接口的 user_id */
  userId: string;
  /** 展示名(花名/真名)，用于头像与标题 */
  displayName: string;
  /** 花名，优先于 realName 展示 */
  nickName?: string;
  /** 真名 */
  realName?: string;
  /** 邮箱，次要展示 */
  email?: string;
}

/**
 * 员工目录搜索能力：按花名/工号/邮箱模糊搜索 Antbu 员工目录。
 * Open Core 默认 null（无员工目录数据源，添加成员弹窗降级为手填工号）；
 * internal overlay 经 src/extensions/internal.ts 注入真实 antbuservice 调用
 * （内部 URL/cookie 仅存在于已剥离的 internal.ts，不进 Open Core 产物）。
 */
export interface UserSearchCapability {
  /** 按关键词搜索员工；关键词为空或目录不可达时返回 []（不抛到 UI）。 */
  search(keyword: string): Promise<SearchedUser[]>;
}

/**
 * 平台指标大盘能力。
 * - url 非空（internal overlay）：AntMonitor 监控大盘 iframe URL，「平台指标」抽屉内嵌入展示（复刻旧 ocb 指标大盘）；
 *   URL 仅存在于内部 overlay（src/internal/help/metricsDashboardCapability.ts），不进 Open Core 产物。
 * - url 为 null（Open Core 默认）：无内部 AntMonitor 数据源，UI 回退静态占位 4 区（PlatformMetricsPanel mock）。
 */
export interface MetricsDashboardSpec {
  /** AntMonitor 监控大盘 URL；null 表示当前形态无大盘数据源，UI 走静态占位回退。 */
  url: string | null;
}

/**
 * 登录策略：决定未登录处置走「ACE 网关登录」还是「外部 OAuth provider 登录」。见 `getLoginStrategy` capability。
 * - `ace-gateway`：员工，跑在 ACE + Tern 后；未登录由 ACE 网关体触发硬跳转（`gateway-login-redirect`）。
 * - `oauth-provider`：Open Core / 外部用户；经 `/auth/*` OAuth provider 登录，拿 `bcs_session` 会话。
 * Open Core 默认 `oauth-provider`；internal overlay 经 `src/extensions/internal.ts` 覆盖为 `ace-gateway`。
 */
export type LoginStrategy = 'ace-gateway' | 'oauth-provider';

/**
 * Bot 工坊引擎「可选清单」单项（筛选下拉、创建弹窗共用）。value 与后端 engine 字段
 * 字符串契约一致（openclaw/aicoding/hermes/teclaw），label 为两处消费方
 * 共用的展示文案。选项见 `getBotEngineOptions` capability。
 */
export interface BotEngineOption {
  value: string;
  label: string;
}

/** 仅 Internal Overlay 提供的 Agent Coding 外部资源；Open Core 全部为 null。 */
export interface AgentCodingInternalResources {
  templateFactoryUrl: string | null;
  imageManualUrl: string | null;
  imageBuildUrl: string | null;
  workflowRepositoryBaseUrl: string | null;
  codefuseTokenUrl: string | null;
  antCodeProjectsApiUrl: string | null;
  antCodeProjectBaseUrl: string | null;
}

/**
 * 产品品牌语义（名称/页头 Logo/登录视觉）。Open Core 业务代码 不得硬编码产品名或
 * logo 实现，统一经 `getProductBrand` 解析；internal overlay 注入 TeamClaw 现状视觉。
 * Logo 用组件承载：字符串 URL 表达不了内部「色块 + 图标」组合样式，且把形态差异
 * 完全封进 overlay 文件，将来新增形态零消费方改动。
 */
export interface ProductBrand {
  /** 产品名；运行时品牌文案（弹窗标题/欢迎语/toast）以插值消费。 */
  name: string;
  /** 页头品牌视觉组件（接收 className 控制尺寸）。 */
  Logo: ComponentType<{ className?: string }>;
  /** 登录态/空态视觉组件（可选，如方版 mark）；缺省时 UI 回退 Logo。 */
  loginWordmark?: ComponentType<{ className?: string }>;
}

/**
 * 个人空间初始化接口的附加 body 选项（部署形态差异收口）。后端
 * /openapi/v1/bots/spaces/personal/initialize 的 body 契约随部署形态不同：
 * - Open Core（阿里云部署）：后端要求 body 携带 skipSC:true 才能完成初始化；
 * - 内部版：后端不消费该字段，不追加（保持历史「请求体为空」契约）。
 * Open Core 默认 { skipSC: true }（defaultCapabilities）；internal overlay 覆盖为 {}。
 */
export interface PersonalSpaceInitOptions {
  /** 阿里云部署形态后端要求的标志位；内部版不传。 */
  skipSC?: boolean;
}

/**
 * 壳层入口形态级可见性（见 `getShellVisibility` capability）。
 * - `adminEntry`：侧栏【管理后台】导航项与 /admin 路由可达性（直访经 getRuntimeRouteRedirect 收敛）
 * - `spaceSwitcher`：侧栏管理区域底部的空间切换器（隐藏不影响 initSpaceContext 空间数据链路）
 * - `notificationBell`：页头右上角通知中心（未读轮询随组件不挂载自然停止）
 */
export interface ShellVisibility {
  adminEntry: boolean;
  spaceSwitcher: boolean;
  notificationBell: boolean;
}

export interface AppCapabilities {
  getHelpLinks: () => CapabilityResult<HelpLink[]>;
  openExternal: (href: string) => CapabilityResult<null>;
  getRuntimeRouteRedirect: (context: RuntimeRouteContext) => CapabilityResult<string | null>;
  getBotHealthCapability: () => CapabilityResult<BotHealthCapability>;
  getCurrentOpenApiUserId: (context?: CurrentUserContext) => CapabilityResult<string | undefined>;
  /**
   * 当前登录 human 身份（花名/头像/在线）。Open Core 默认读 workspaceStore.identities
   * 的 me（listMyBots human[0]）；内部 overlay 读 staff_id cookie + __TERN__.user。
   * 返回 null = 未登录/未就绪；status==='unsupported' = 当前形态不支持解析。
   * 同步签名：capability 不发请求，返回当前已解析缓存；异步解析由 useHumanIdentity 编排。
   */
  getHumanIdentity: () => CapabilityResult<HumanIdentity | null>;
  /**
   * 版本发布说明能力。Open Core 默认 null（不支持，菜单不渲染该项）；
   * internal overlay 经 src/internal/help 提供 yuyan 配置实现。
   */
  getReleaseNotesCapability: () => CapabilityResult<ReleaseNotesCapability | null>;
  /**
   * 员工目录搜索能力（花名/工号/邮箱）。Open Core 默认 null（不支持，添加成员弹窗降级为手填工号）；
   * internal overlay 经 src/extensions/internal.ts 注入 antbuservice 实现。
   */
  getUserSearchCapability: () => CapabilityResult<UserSearchCapability | null>;
  /**
   * 按 userId（工号）解析空间成员头像 URL。
   * Open Core 默认返回 null（UI 回退首字母占位）；internal overlay 按工号拼 antwork 照片 URL，
   * 与 getHumanIdentity 的头像兜底同源（buildAntworkAvatarUrl）——内网 URL 常量只在
   * src/extensions/internal.ts（.internal-paths 剥离），不进 Open Core 产物。
   * 返回 null = 当前形态无成员头像数据源，UI 走首字母占位。
   */
  getMemberAvatarUrl: (userId: string) => CapabilityResult<string | null>;
  /**
   * 平台指标大盘。Open Core 默认 url=null（「平台指标」抽屉回退静态占位 4 区）；
   * internal overlay 注入 AntMonitor 监控大盘 URL（iframe 嵌入，复刻旧 ocb 指标大盘）。
   * 同步签名：capability 不发请求，直接返回当前形态的大盘 URL（或 null 回退）。
   */
  getMetricsDashboard: () => CapabilityResult<MetricsDashboardSpec>;
  /** 应用 Coding 的语雀 Token 获取指引视频；Open Core 默认不提供，internal overlay 注入。 */
  getAppCodingYuqueTokenGuideVideoUrl: () => CapabilityResult<string | null>;
  /** CodeFuse 模型目录接口地址；Open Core 无内部目录时返回 null。 */
  getCodefuseModelsUrl: () => CapabilityResult<string | null>;
  getAgentCodingInternalResources: () => CapabilityResult<AgentCodingInternalResources>;
  /**
   * 登录策略（见 `LoginStrategy`）：Open Core 默认 `oauth-provider`；internal overlay `ace-gateway`。
   * `status==='unsupported'` 时调用方按 `ace-gateway` 兜底。同步签名，不发请求。
   */
  getLoginStrategy: () => CapabilityResult<LoginStrategy>;
  /**
   * 内部专属侧栏一级导航项（Open Core 不应展示的内部入口）。
   * Open Core 默认 `[]`（不渲染任何内部导航项，符合 open-core-export-plan §5.2
   * 「导航中的内部入口」必须按开源模式分隔的强约束）；
   * internal overlay 经 `src/extensions/internal.ts` 注入「能力工坊」「能力市场」两项，
   * 字面量与图标实例随 overlay 物理剥离（`.internal-paths`），不进 Open Core 产物。
   * AppShell 合并 `navigationItems`（Open Core 基线）与本能力返回值后渲染侧栏。
   */
  getInternalNavigationItems: () => CapabilityResult<NavigationItem[]>;
  /**
   * 内部专属 route meta 列表（Open Core 不应收录的内部路由元数据）。
   * Open Core 默认 `[]`（与 `routeMetaList` 已剥离内部项一致）；
   * internal overlay 注入 `/capability-workshop/*`、`/market/*` 等内部路由 meta，
   * 字面量随 overlay 物理剥离，不进 Open Core 产物。
   * `getRouteMeta` 消费本能力与基线 `routeMetaList` 合并后的链表做高亮/分组解析。
   */
  getInternalRouteMetas: () => CapabilityResult<RouteMeta[]>;
  /**
   * Bot 工坊引擎可选清单（筛选下拉、创建弹窗的唯一事实源，组件不得自行硬编码）。
   * Open Core 默认 `openclaw` + `claude_code`（保留原生 CC 直建入口，阿里云部署依赖）；
   * internal overlay 覆盖为 4 项（CC 创建由 AgentCoding 接管，剥离原生入口）。
   * 注意：引擎领域映射规则（服务化矩阵、cluster_name、AgentCoding 家族、WS 路径等）
   * 是后端契约事实，MUST 保留全量，不随本清单收窄。
   */
  getBotEngineOptions: () => CapabilityResult<BotEngineOption[]>;
  /**
   * 产品品牌语义（名称/页头 Logo/登录视觉，见 `ProductBrand`）。
   * Open Core 默认 `Avernet` + 横版 wordmark；internal overlay 覆盖为 `TeamClaw` +
   * 现状蓝底 AppWindow 视觉（样式块随 overlay 剥离，Open Core 产物不背内部样式）。
   */
  getProductBrand: () => CapabilityResult<ProductBrand>;
  /**
   * 初始化个人空间接口的附加 body 选项（见 `PersonalSpaceInitOptions`；部署形态差异收口点）。
   * 统一由 adminService.ensurePersonalSpace 消费注入 Controller，Controller 不感知形态。
   * 同步签名，不发请求。
   */
  getPersonalSpaceInitOptions: () => CapabilityResult<PersonalSpaceInitOptions>;
  /**
   * 壳层入口可见性（管理后台导航 / 空间切换器 / 通知中心，见 `ShellVisibility`）。
   * Open Core（阿里云部署）默认三项全 false（defaultCapabilities）；
   * internal overlay 经 `src/extensions/internal.ts` 覆盖为全 true——内部形态渲染结果与改造前一致。
   * 同步签名，不发请求。消费方（navigation / SidebarNavList / AppHeader）不得以 `if (isInternal)` 替代。
   */
  getShellVisibility: () => CapabilityResult<ShellVisibility>;
}
