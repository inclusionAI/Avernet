/**
 * app 层契约类型：模块清单 / 菜单 / 路由 / 认证 / 资源。
 * 设计见 docs/specs/capabilities-system.md（案例 D/F + AppExt）。
 */
import type { GroupInfo } from '@/pages/GroupChat/types';
import type {
  AntbuUserInfo,
  UserInfo,
} from '@/services/backend-api/UserController';
import type { ComponentType } from 'react';
import type { EnvName } from '../../config/servers.config';

/** 主导航菜单项（形态贴合现 MainLayout TABS：icon/label/path/order/beta）。 */
export interface MenuManifest {
  /** lucide 图标组件（运行时类型，避免在 app 层耦合 React 类型） */
  icon?: unknown;
  /** 现状为字面文案；i18n 落地后改为 labelKey */
  label: string;
  path: string;
  /** 菜单排序，越小越靠前 */
  order: number;
  beta?: boolean;
  /** 分组（用于菜单分隔符：组切换处插入 divider） */
  group?: 'main' | 'market';
}

/** umi 路由配置（透传 spmBPos 等附加字段）。 */
export interface RouteManifest {
  name?: string;
  path: string;
  component?: string;
  layout?: boolean;
  routes?: RouteManifest[];
  redirect?: string;
  hideInMenu?: boolean;
  [key: string]: unknown;
}

/** 模块自登记清单。一个模块 = 一份 manifest，决定它的路由与菜单。 */
export interface ModuleManifest {
  id: string;
  /** capability 开关；缺省 true。false → 路由不注册、菜单不显示 */
  enabled?: boolean;
  route?: RouteManifest;
  menu?: MenuManifest;
}

/** buildMenus 产出的菜单项（manifest.menu + 模块 id）。 */
export interface MenuItem extends MenuManifest {
  id: string;
}

/**
 * 当前 human 身份（差异类型 C 的载荷）。
 * 开源默认走 /auth/user 解析；内部 extend 读 window.__TERN__.user。
 */
export interface HumanIdentity {
  /** 用户唯一标识（开源=user_id / 内部=outUserNo） */
  userId: string;
  /** 展示名（花名 / 昵称） */
  nickName: string;
  /** 头像 URL，可空 */
  avatarUrl?: string;
}

/**
 * 功能开关（差异类型 A：值 flag map）。
 * 内部专属三项（画像公开 / 融合模式 / 高级设置）已升级为 slots（组件注入契约），
 * 不再使用 flag 门控，见 docs/重构/一期BCN开源/BCN 内部组件代码不可见隔离方案.md。
 * 由 src/internal/features.ts extend 覆盖为内部取值。
 */
export interface FeatureFlags {
  /**
   * 顶栏「创建 Bot」接入引导入口（TopNavBar 的创建 Bot tab + AddBotGuideModal）。
   * 开源专属：开源 true / 内部 false（内部走工作台 BotManager 创建 Bot，不在协作页透出此入口）。
   */
  bcnBotOnboarding: boolean;
  /**
   * BCN 闭包模式：开源 BCN 页面不调 /api（业务请求只走 /bcnproxy）。
   * 开源 true：GroupChat 初始化不调 loadBots（/api/bots/by-owner-or-collaborator），
   *   改由 initUnifiedBotTabs 内部 /bcnproxy/bots/my 驱动并反写 botStore。
   * 内部 false：保留原有 loadBots → convertBotsToTabItems → localBots 路径不变。
   */
  bcnProxyOnly: boolean;
}

/**
 * 组件注入插槽的 props 契约（差异类型「实现/组件注入」）。
 * 内部专属组件下沉 src/internal/，开源默认 null=不渲染；内部 src/internal/slots.ts extend 注入真实组件。
 */
export interface AdvancedSettingsSlotProps {
  onActiveOnlyChange?: (activeOnly: boolean) => void | Promise<void>;
  onRepairComplete?: () => void;
}
export interface FuseSlotProps {
  group: GroupInfo | null;
  /** 当前活跃会话 ID，用于按 session 维度判断/清除未读 */
  activeSessionId: string | null;
}
export interface GroupVisibilitySlotProps {
  group: GroupInfo;
  isOwner: boolean;
}
export interface BotProfilePublicSlotProps {
  botUuid?: string;
}

/**
 * 组件注入插槽（差异类型「实现/组件注入」）。
 * 三个内部专属功能（高级设置 / 融合模式 / Bot 画像公开）的组件由内部注入，
 * 开源默认全 null（不渲染、组件代码不进开源闭包）；内部 src/internal/slots.ts extend 注入。
 * 见 docs/重构/一期BCN开源/BCN 内部组件代码不可见隔离方案.md。
 */
export interface AppSlots {
  /** 顶栏高级设置（TopNavBar：仅活跃 Bot + 自助修复）。内部专属，开源 null */
  advancedSettings: ComponentType<AdvancedSettingsSlotProps> | null;
  /** 协作群会话融合模式悬浮问答（GroupChatPage）。内部专属，开源 null */
  fuseChat: ComponentType<FuseSlotProps> | null;
  /** 群设置-Bot 画像公开 section（GroupSettingsDrawer）。内部专属，开源 null */
  groupVisibility: ComponentType<GroupVisibilitySlotProps> | null;
  /** BotInfoCard 画像公开开关（含确认弹窗）。内部专属，开源 null */
  botProfilePublic: ComponentType<BotProfilePublicSlotProps> | null;
}

/**
 * 认证适配器（差异类型 C：实现替换）。
 * 开源默认 token 实现；内部 extend 为 SsoAdapter。具体实现属内部能力。
 */
export interface AuthAdapter {
  signIn(): Promise<void>;
  signOut(): void;
  getCurrentToken(): string | null;
  refresh(): Promise<void>;
  /** 当前 human 身份；开源默认走 /me，内部 extend 读 __TERN__.user。null = 未登录 */
  getCurrentUser(): Promise<HumanIdentity | null>;
}

/** 资源 URL（差异类型 G：null = 该入口不渲染）。 */
export interface Resources {
  userManualUrl: string | null;
  releaseNotesKey: string | null;
  customerServiceBotId: string | null;
  /** 桌面端下载/接入参考文档；null = 链接不渲染（仅展示纯文本提示） */
  desktopDownloadUrl: string | null;
  /** 「发布到广场」权限说明文档；null = 入口不渲染 */
  publicSettingDocUrl: string | null;
  /** BCN 引擎 Skill 安装指令（含私有 registry）；null = 安装步骤不渲染 */
  bcnInstallCmd: string | null;
  /** 《BCN 产品快速上手》文档链接；null = 入口不渲染 */
  bcnQuickStartUrl: string | null;
  /** 《BCN 详细接入指南》文档链接；null = 入口不渲染 */
  bcnGuideUrl: string | null;
  /** BCN 协作模板「查看文档」链接；null = 入口不渲染 */
  bcnCollaborationDocUrl: string | null;
  /** 「什么是协作网络」说明链接；null = 入口不渲染 */
  bcnNetworkIntroUrl: string | null;
  /**
   * 答疑机器人入口链接（移动端导航 / 顶栏帮助入口）；null = 入口不渲染。
   * 内部为即时通讯客户端的 scheme 链接（含会话 token），经 src/internal/resources.ts 注入。
   */
  customerServiceRobotUrl: string | null;
  /**
   * 本地 Bot 接入指令模板（含 `{token}` 占位，运行时用注册 token 替换）；
   * null = 接入指令区不渲染。含私有 registry 的内部模板经 src/internal/resources.ts 注入。
   */
  bcnConnectCmdTemplate: string | null;
  /**
   * Bot 自动接入指令模板（产品首页「方式2：Bot 自动接入」，含 `{token}` 占位）；
   * null = 该卡不渲染。与 bcnConnectCmdTemplate 并列，对应「用户自助接入」之外的第二种接入方式。
   */
  bcnAutoConnectCmdTemplate: string | null;
  /** 产品获取链接（移动端导航「产品获取」区）；各项 null = 该项不渲染 */
  productLinks: {
    tui: string | null;
    mobile: string | null;
    desktop: string | null;
  };
}

/**
 * 埋点适配器（差异类型 C：实现替换）。
 * 开源默认 no-op；内部 extend 为 Tracert 实现。
 */
export interface TrackerAdapter {
  click(spm: string, params?: Record<string, unknown>): void;
  expo(spm: string, direction?: string, params?: Record<string, unknown>): void;
  config(options: Record<string, unknown>): void;
}

/**
 * 异常上报适配器（差异类型 C：实现替换）。
 * 开源默认 no-op；内部 src/internal/errorReporter.ts extend 走内部监控（含上报 code）。
 * 与 trackerAdapter 平行——专用于 WS 异常等需要带结构化诊断上下文的上报。
 */
export interface ErrorReporter {
  /**
   * 上报 WebSocket 异常。
   * @param payload msg=人读摘要；detail/context=序列化后的诊断 JSON（c1/c2）
   */
  reportWsError(payload: {
    msg: string;
    detail: string;
    context: string;
  }): void;
}

/**
 * 用户目录适配器（差异类型 C：实现替换）。
 * 开源默认 no-op 返回 []；内部 extend 走 antbuservice 员工目录。
 */
export interface UserDirectoryAdapter {
  getUsersByIds(
    userIds: string[],
    options?: Record<string, unknown>,
  ): Promise<UserInfo[]>;
  searchUsers(
    keyword: string,
    options?: Record<string, unknown>,
  ): Promise<AntbuUserInfo[]>;
}

/**
 * 运行期环境判断适配器（差异类型 C：实现替换）。
 * 由 hostname 推断环境；开源默认恒返回 null（不靠 hostname 特征猜，统一 fallback）。
 * 内部 extend 注入按 hostname 特征的真实环境判断。
 */
export interface EnvResolver {
  /** 由 hostname 推断环境；返回 null 表示无法判断，调用方走默认值 */
  resolveEnvFromHostname(hostname: string): EnvName | null;
}

/**
 * BCN 专属域名判断适配器（差异类型 C：实现替换）。
 * 开源默认恒 false（无 BCN 域名门禁，所有路径放行）。
 * 内部 extend 注入 BCN 专属域名的等值判断。
 */
export interface BcnDomain {
  /** 当前 hostname 是否为 BCN 专属域名 */
  isBcnHostname(hostname: string): boolean;
}

/**
 * 本地开发环境判断（差异类型 C：实现替换）。AppExt.env 字段的形状。
 * 开源默认恒 false（开源无本地开发 SSO 旁路）；
 * 内部 src/internal/env.ts extend 读 window.__TERN__._localDev。
 */
export interface EnvCapability {
  /** 是否为内部本地开发模式（决定 SSO/Bot 初始化的旁路时序） */
  isLocalDev(): boolean;
}

/**
 * IAM token 拉取（差异类型 C：实现替换）。AppExt.iam 字段的形状。
 * 与 SSO token（AuthAdapter）平行的第二条 token 链路（GroupChat 重连刷新用）。
 * 开源默认恒返回空串（无内部 IAM 接口）；
 * 内部 src/internal/bootstrap/iam.ts extend 走 /api/v1/token/iam。
 */
export interface IamCapability {
  /** 拉取 IAM token 并写入 userStore；失败或开源默认返回空串 */
  fetchIamToken(): Promise<string>;
}

/** render 编排上下文：oldRender 为 umi 原始渲染入口，由各分支在合适时机调用 */
export interface BootstrapInitContext {
  oldRender: () => void;
}

/** getInitialState 产出的初始身份态（透传给 umi useModel('@@initialState')） */
export interface InitialAuthState {
  userInfo: unknown;
}

/**
 * Bootstrap 控制面（差异类型 C：实现替换）。AppExt.bootstrap 字段的形状。
 * 开源默认：init 直接 oldRender()（跳过一切 SSO/IAM/访问控制/Bot 编排）、
 *           fetchInitialAuth 返回空壳身份（不请求后端）。
 * 内部 src/internal/bootstrap.ts extend 覆盖为完整 SSO + IAM + 访问控制 +
 *      Bot / CloudBrain / VSCode 初始化编排（含 __TERN__ 读取、getCachedUserInfo 兜底）。
 */
export interface BootstrapControl {
  /** 启动编排；开源默认直接 oldRender，内部为完整 render 分支 */
  init(ctx: BootstrapInitContext): void;
  /** 初始身份获取；开源默认空壳，内部为 fetchIamToken + __TERN__.user + 缓存兜底 */
  fetchInitialAuth(): Promise<InitialAuthState>;
}
