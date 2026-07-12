/**
 * 应用级契约（跨模块共享的可变面）。
 * 模块清单 / 认证适配器 / 资源 URL —— 开源默认值在此，内部经 extend 覆盖。
 * 写法规范见 docs/specs/capabilities-system.md（§3 规范 3：跨模块契约升到 app/extension.ts）。
 */
import { defineExt } from '@/capabilities';
import * as BcnController from '@/services/backend-api/BcnController';
import { Bell, Bot, Clock, Globe, User } from 'lucide-react';
import type { ServerConfigMap } from '../../config/servers.config';
import { SERVERS } from '../../config/servers.config';
import type {
  AppSlots,
  AuthAdapter,
  BcnDomain,
  BootstrapControl,
  EnvCapability,
  EnvResolver,
  ErrorReporter,
  FeatureFlags,
  IamCapability,
  ModuleManifest,
  Resources,
  TrackerAdapter,
  UserDirectoryAdapter,
} from './types';

/** 开源默认认证适配器：无 SSO，token 由运行时另行注入（内部 extend 为 SsoAdapter）。 */
const defaultAuthAdapter: AuthAdapter = {
  async signIn() {},
  signOut() {},
  getCurrentToken() {
    return null;
  },
  async refresh() {},
  /** 开源默认 human 身份：走 /bcnproxy/me；未登录后端字段全 null → 返回 null。 */
  async getCurrentUser() {
    try {
      const u = await BcnController.getMe();
      return u?.staff_no
        ? { userId: u.staff_no, nickName: u.nick_name || u.staff_no }
        : null;
    } catch (error) {
      console.error('[defaultAuthAdapter] getCurrentUser', error);
      return null;
    }
  },
};

const hermesBcnInstallerCommand =
  '( set -e; installer_url="https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/docs/install-instructions/install-hermes.sh"; installer_fallback_url="https://api.github.com/repos/inclusionAI/Avernet/contents/src/bcs/docs/install-instructions/install-hermes.sh?ref=dev"; raw_base="https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/connectors/hermes"; installer="$(mktemp "${TMPDIR:-/tmp}/install-hermes.XXXXXX")"; trap \'rm -f "$installer"\' EXIT; if ! curl --ipv4 --fail --silent --show-error --location --retry 1 --retry-all-errors --connect-timeout 10 --max-time 15 "$installer_url" -o "$installer"; then curl --ipv4 --fail --silent --show-error --location --retry 3 --retry-all-errors --connect-timeout 10 --max-time 30 -H "Accept: application/vnd.github.raw+json" "$installer_fallback_url" -o "$installer"; fi; printf \'%s\\n\' \'{token}\' | env AVERNET_RAW_BASE_URL="$raw_base" BCS_INSTALLER_URL="$installer_url" bash "$installer" --human-token-stdin --bot-name {bot_name} --profile {profile} --create-profile --china-mirror )';

export const AppExt = defineExt('App', {
  /**
   * 模块清单（驱动主导航菜单 buildMenus）。开源默认 = 对外可见的模块。
   * 内部模块（服务Bot / Bot广场 / 公开市场）经 src/internal/navModules.ts extend 追加。
   * order 给内部项留位：servicebot=2 / expertmarket=20 / market=21。
   */
  modules: [
    {
      id: 'assistant',
      menu: {
        icon: Bot,
        label: '我的 Bot',
        path: '/assistant',
        order: 1,
        group: 'main',
      },
    },
    {
      id: 'privatechat',
      menu: {
        icon: User,
        label: '我的互动',
        path: '/private-chat',
        order: 3,
        group: 'main',
      },
    },
    {
      id: 'groupchat',
      menu: {
        icon: Globe,
        label: '我的协作',
        path: '/group-chat',
        order: 4,
        group: 'main',
        beta: true,
      },
    },
    {
      id: 'cron',
      menu: {
        icon: Clock,
        label: '我的任务',
        path: '/cron/list',
        order: 5,
        bcnInstallCmd: null,
        bcnQuickStartUrl: null,
        bcnGuideUrl: null,
        bcnCollaborationDocUrl: null,
        bcnNetworkIntroUrl: null,
        group: 'main',
      },
    },
    {
      id: 'notify',
      menu: {
        icon: Bell,
        label: '我的消息',
        path: '/notify/list',
        order: 6,
        group: 'main',
      },
    },
  ] as ModuleManifest[],

  /** 认证适配器（差异类型 C） */
  authAdapter: defaultAuthAdapter,

  /**
   * 功能开关（差异类型 A）：内部专属三项已升级为 slots（组件注入契约），
   * 此处保留开源专属 bcnBotOnboarding + BCN 闭包 bcnProxyOnly。
   * 内部 src/internal/features.ts extend 覆盖为相应取值。
   */
  features: {
    bcnBotOnboarding: true,
    bcnProxyOnly: true,
  } as FeatureFlags,

  /**
   * 组件注入插槽（差异类型「实现/组件注入」）：开源默认全 null（三个内部专属功能不渲染、
   * 组件代码不进开源闭包），内部 src/internal/slots.ts extend 注入真实组件。
   * 见 docs/重构/一期BCN开源/BCN 内部组件代码不可见隔离方案.md。
   */
  slots: {
    advancedSettings: null,
    fuseChat: null,
    groupVisibility: null,
    botProfilePublic: null,
  } as AppSlots,

  /** 埋点适配器（差异类型 C）：开源默认 no-op，内部 extend 注入 Tracert */
  trackerAdapter: {
    click() {},
    expo() {},
    config() {},
  } as TrackerAdapter,

  /** 异常上报适配器（差异类型 C）：开源默认 no-op，内部 src/internal/errorReporter.ts extend 走内部监控 */
  errorReporter: {
    reportWsError() {},
  } as ErrorReporter,

  /** 资源 URL（差异类型 G，null = 入口不渲染）；BCN 内部链接/命令经 src/internal/resources.ts extend */
  resources: {
    userManualUrl: null,
    releaseNotesKey: null,
    customerServiceBotId: null,
    desktopDownloadUrl: null,
    publicSettingDocUrl: null,
    // 答疑机器人入口（移动端导航等）：开源默认 null（入口不渲染）；
    // 内部 src/internal/resources.ts 注入钉钉 scheme 链接（含 token）。
    customerServiceRobotUrl: null,
    // 开源默认：产品首页双接入方式的公共指令模板（含 {token} 占位，运行时注入注册 token）；
    // 内部 src/internal/resources.ts 可覆盖为含私有 registry 的内网指令。
    // 方式1：用户自助接入
    bcnConnectCmdTemplate:
      'bash -c "$(curl -fsSL https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/docs/install-instructions/install.sh || echo "exit 1")" install.sh --token {token}',
    // 方式2：Bot 自动接入
    bcnAutoConnectCmdTemplate:
      'Follow the instructions in https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/docs/install-instructions/install.md to join BCN (Bot Coordination Network), your token is {token}',
    bcnHermesConnectCmdTemplate: hermesBcnInstallerCommand,
    bcnHermesAutoConnectCmdTemplate: `Execute this exact command in a local shell without using another BCN integration method:\n${hermesBcnInstallerCommand}`,
    productLinks: { tui: null, mobile: null, desktop: null },
  } as Resources,

  /** 用户目录适配器（差异类型 C）：开源默认 no-op 返回 []，内部 extend 走 antbuservice */
  userDirectory: {
    async getUsersByIds() {
      return [];
    },
    async searchUsers() {
      return [];
    },
  } as UserDirectoryAdapter,

  /**
   * 服务器地址（差异类型 C）：开源默认 = 占位 SERVERS（零内网域名），
   * 内部经 src/internal/servers.ts extend 注入真实 SERVERS（来自 config/servers.internal.ts）。
   */
  servers: SERVERS as ServerConfigMap,

  /** 运行期环境判断（差异类型 C）：开源默认恒返回 null，内部 extend 注入 hostname 判断 */
  envResolver: {
    resolveEnvFromHostname: () => null,
  } as EnvResolver,

  /** BCN 域名判断（差异类型 C）：开源默认恒 false，内部 extend 注入 BCN 专属域名判断 */
  bcnDomain: {
    isBcnHostname: () => false,
  } as BcnDomain,

  /**
   * 本地开发环境判断（差异类型 C）：开源默认恒 false（无本地开发 SSO 旁路），
   * 内部 src/internal/env.ts extend 读 window.__TERN__._localDev。
   */
  env: {
    isLocalDev: () => false,
  } as EnvCapability,

  /**
   * IAM token 拉取（差异类型 C）：开源默认恒返回空串，
   * 内部 src/internal/bootstrap/iam.ts extend 走 /api/v1/token/iam。
   * 与 authAdapter（SSO token）平行的第二条 token 链路。
   */
  iam: {
    fetchIamToken: async () => '',
  } as IamCapability,

  /**
   * Bootstrap 控制面（差异类型 C）：开源默认 init 直接 oldRender（跳过一切认证）、
   * fetchInitialAuth 返回空壳；内部 src/internal/bootstrap.ts extend 为完整
   * SSO + IAM + 访问控制 + Bot/CloudBrain/VSCode 编排。
   */
  bootstrap: {
    init: (ctx) => {
      ctx.oldRender();
    },
    fetchInitialAuth: async () => ({ userInfo: { clientUser: {} } }),
  } as BootstrapControl,
});
