import type { RequestConfig } from '@umijs/max';
import { toast } from 'sonner';
import {
  hideErrorPrompt,
  showErrorPrompt,
} from './pages/Bootstrap/ErrorPrompt/errorPromptState';
import { showLoginPrompt } from './pages/Bootstrap/LoginPrompt/loginPromptState';
import {
  getProxyTarget,
  getProxyToken,
  isLocalMode,
} from './stores/connectionStore';
import {
  getServiceProxyTarget,
  getServiceProxyToken,
  useServiceConnectionStore,
} from './stores/serviceConnectionStore';
import { useUserStore } from './stores/userStore';
import { getCurrentBotKindContext } from './utils/botScopeContext';
import { getElectronServerUrl } from './utils/env';
import { applyIamTokenHeader } from './utils/iamToken';
import { isDingTalk, isElectron, isVSCode } from './utils/platform';
import {
  extractErrorMessage,
  formatApiPath,
  showRequestError,
} from './utils/requestErrorHandler';
import { Tracert } from './utils/tracert';
import { vscodeRequestAdapter } from './utils/vscodeRequestAdapter';

/**
 * 本地 harness-data 调试开关（仅 dev 注入，见 config.local.ts 的 define）。
 * 为 true 时 /data/* 不走 proxypass，改由 dev server proxy 直连本机
 * harness-data（默认 http://localhost:8765）—— 详见 config.local.ts。
 * 生产构建里该常量为 false（被 define 替换），分支会被 tree-shake 掉。
 */
declare const LOCAL_HARNESS_DATA: boolean | undefined;

/**
 * BCN 一期闭包接口守卫开关（由 config 的 define 注入，见 config.local.ts）。
 * 为 true 时，dev 下对任何 /api 业务请求告警——BCN 一期的业务数据应全部走
 * /bcnproxy（BCN 后端），不应触达 /api（内部 CloudBrain / 管理后端）。
 * 生产构建未注入该常量，typeof 守卫使整段被 tree-shake。
 */
declare const BCN_API_GUARD: boolean | undefined;

// 已告警的 /api 路径去重：轮询类接口会高频命中，避免刷屏；同一 path 只报一次
const reportedBcnApiViolations = new Set<string>();

/**
 * BCN 闭包守卫：dev-only，仅告警不阻断。
 * 业务请求若以 /api 开头即视为违规（/bcnproxy 不以 /api 开头，天然放行；
 * BCN 自有 WebSocket 是 ${bcnServer}/ws，也不以 /api 开头）。
 * 打印调用栈以定位是哪个 hook/useEffect 发起的（如 useBot 的 fetchTotalBotCount
 * 在挂载时自动触发的 /api/bots/by-owner-or-collaborator）。
 */
function reportBcnApiViolation(url: string): void {
  if (typeof BCN_API_GUARD === 'undefined' || !BCN_API_GUARD) return;
  if (!url || !url.startsWith('/api')) return;
  const pathOnly = url.split('?')[0];
  if (reportedBcnApiViolations.has(pathOnly)) return;
  reportedBcnApiViolations.add(pathOnly);
  console.error(
    `[BCN闭包违规] BCN 一期不应调用 /api，业务请求应走 /bcnproxy：${pathOnly}`,
  );
  console.trace('[BCN闭包违规] 调用栈');
}

function isPrivateNetworkHost(host: string): boolean {
  if (!host) return false;
  if (host.includes('localhost')) return true;
  return false;
}

/**
 * 判断错误是否是因为浏览器拦截了对本地设备的访问（Chrome PNA）
 * - 请求未拿到 response
 * - axios 标记为 Network Error / ERR_NETWORK
 * - 目标 host 是本机 / 私有网段
 */
function isLocalNetworkBlocked(error: any, requestUrl: string): boolean {
  const code = error?.code || '';
  const msg = error?.message || '';
  const isNetworkErr =
    code === 'ERR_NETWORK' ||
    /network error/i.test(msg) ||
    /failed to fetch/i.test(msg);
  if (!isNetworkErr) return false;
  // 解析 host：兼容绝对 URL 和形如 //host/path 的字符串
  let host = '';
  try {
    if (/^https?:\/\//i.test(requestUrl)) {
      host = new URL(requestUrl).hostname;
    } else {
      const m = requestUrl.match(/^\/\/([^/]+)/);
      if (m) host = m[1].split(':')[0];
    }
  } catch {
    host = '';
  }
  return isPrivateNetworkHost(host);
}

/**
 * 弹出 PNA 授权引导弹窗（同一 session 内去重）
 * 大量 hook 都加了 skipErrorHandler，PNA 阻断会让每次请求都进入此分支，需要去重避免反复弹窗。
 */
function showLocalNetworkBlockedPrompt() {
  const siteOrigin = window.location.protocol + '//' + window.location.host;
  const chromeSettingsUrl = `chrome://settings/content/siteDetails?site=${encodeURIComponent(
    siteOrigin,
  )}`;
  showErrorPrompt({
    title: '需要允许访问本地设备',
    description:
      '浏览器出于安全策略拦截了对本地设备的访问请求。请按以下步骤在 Chrome 中授权当前站点：',
    steps: [
      {
        text: '复制下方链接并粘贴到 Chrome 地址栏，按回车打开站点权限页',
        copyable: chromeSettingsUrl,
      },
      { text: '在权限列表中找到「设备上的应用」，切换为「允许」' },
      { text: '回到本页面并点击「刷新页面」' },
    ],
    dismissible: true,
  });
}

export const request: RequestConfig = {
  withCredentials: true,
  requestInterceptors: [
    (config: any) => {
      const { url, proxyTarget, proxyToken, ...options } = config;
      const headersWithIamToken = applyIamTokenHeader(options.headers);

      // BCN 一期闭包守卫（dev-only，仅告警）：看原始 url，在任何代理改写之前判定
      reportBcnApiViolation(url);

      // VSCode 扩展环境：所有请求通过 postMessage 转发给扩展宿主代理
      if (isVSCode()) {
        return {
          ...config,
          headers: {
            ...headersWithIamToken,
            'X-SSO-TOKEN': useUserStore.getState().accessToken || '',
          },
          adapter: vscodeRequestAdapter,
        };
      }

      // Electron 桌面端：所有 /api 与 /data 请求走本地 Agent
      // （/data 是 data-proxy 通道，本地 Agent 即本机引擎 :20003 的 /data router）
      if (isElectron() && (url.startsWith('/api') || url.startsWith('/data'))) {
        return {
          url: `${getElectronServerUrl()}${url}`,
          ...options,
          headers: {
            ...headersWithIamToken,
            'X-SSO-TOKEN': useUserStore.getState().accessToken || '',
          },
        };
      }

      // 判断是否在服务 Bot 页面（通过 URL 路径）
      const isServiceBotPage =
        typeof window !== 'undefined' &&
        (window.location.pathname.startsWith('/service/detail') ||
          window.location.pathname.startsWith('/service/chat') ||
          window.location.pathname.startsWith('/service/view'));

      // 获取对应的连接信息
      // 服务 Bot 页面优先使用 serviceConnectionStore，未就绪时 fallback 到全局 connectionStore
      const serviceProxyTarget = isServiceBotPage
        ? getServiceProxyTarget()
        : '';
      const activeProxyTarget = serviceProxyTarget || getProxyTarget();
      const activeProxyToken =
        isServiceBotPage && serviceProxyTarget
          ? getServiceProxyToken()
          : getProxyToken();
      // connectionType 需与 target 来源一致：服务 Bot 页面用 serviceConnectionStore 的 type
      const activeIsLocal =
        isServiceBotPage && serviceProxyTarget
          ? useServiceConnectionStore.getState().connectionType === 'local'
          : isLocalMode();

      // Web 端：业务接口（/api/sessions、/api/engine、/api/models、/api/approvals）走 CloudBrain proxy
      // /api/v1 的云端大脑管理接口走 dev server proxy，不在此处处理
      const urlPrefix = [
        '/api/sessions',
        'api/openclaw',
        '/api/teclaw',
        '/api/engine',
        '/api/notify',
        // '/api/cron', // NOTE:  0329 cron不需要前缀
        '/api/models',
        '/api/approvals',
        '/api/nodes',
        // AICoding 第三栏接口 - Engine 直连
        '/api/aicoding/sessions',
        // data-proxy 通道：前端发 /data/*，与其它业务接口一致走 proxypass。
        // proxypass 的 target 是引擎适配器 :20003，引擎用 prefix=/data 的 router
        // 接收并反代到 harness-data；故这里必须用 /data 而非 /api/aicoding/data-proxy。
        '/data/',
      ];

      // 本地 harness-data 调试：dev 下设 LOCAL_HARNESS=1 时，/data/* 不走
      // proxypass，原样透出由 dev server 的 proxy 规则直连本机 harness-data
      // (:8765)。仅 dev 注入该常量；生产恒 false，本分支被 tree-shake。
      const isDataProxy = url.startsWith('/data/');
      if (
        typeof LOCAL_HARNESS_DATA !== 'undefined' &&
        LOCAL_HARNESS_DATA &&
        isDataProxy
      ) {
        return {
          ...config,
          headers: headersWithIamToken,
        };
      }

      if (
        !isElectron() &&
        urlPrefix.some((prefix) => url.startsWith(prefix)) &&
        activeProxyTarget
      ) {
        const target = proxyTarget || activeProxyTarget;
        const token = proxyToken || activeProxyToken;

        // 判断是否为本地模式（服务端返回 type: "local"）
        if (activeIsLocal) {
          // 本地模式：直接连接到本地大脑，不走 /proxypass/
          // target 格式：127.0.0.1:20001
          return {
            url: `http://${target}${url}`,
            ...options,
            headers: {
              ...headersWithIamToken,
              // 本地模式 token 可能为空，根据实际情况传递
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
          };
        } else {
          // 云端模式：通过 /proxypass/ 代理连接
          return {
            url: `/proxypass/${target}${url}`,
            ...options,
            headers: {
              ...headersWithIamToken,
              'X-PROXYPASS-TOKEN': token,
            },
          };
        }
      }

      return {
        ...config,
        headers: headersWithIamToken,
      };
    },
  ],
  responseInterceptors: [
    (response: any) => {
      const data = response.data;
      const url = response.config?.url || '';
      // checkAccess 接口临时兼容：后端直接返回 {label, operator}，前端补全标准格式
      // 后端返回：{"label":1,"operator":"yubai.zly"}
      // 补全为：{success: true, data: {label:1, operator:"yubai.zly"}}
      // TODO: 待后端修复为标准格式后移除此兼容逻辑
      if (url.includes('/api/v1/access/check')) {
        if (
          data &&
          typeof data.label !== 'undefined' &&
          typeof data.success === 'undefined'
        ) {
          response.data = {
            success: true,
            data: {
              label: data.label,
              operator: data.operator,
            },
          };
        }
      }

      if (
        data?.buserviceErrorCode === 'USER_NOT_LOGIN' ||
        data?.actionType === 'LOGIN'
      ) {
        if (isDingTalk() && data.buserviceErrorMsg) {
          // 如果是钉钉第四屏，不要弹窗直接跳转
          window.location.href = data.buserviceErrorMsg;
        } else {
          hideErrorPrompt();
          showLoginPrompt(data.buserviceErrorMsg || '', data.help);
          return response;
        }
      }
      // 业务错误统一提示（调用方可传 skipErrorHandler: true 跳过）
      if (data?.success === false && !response.config?.skipErrorHandler) {
        const apiPath = formatApiPath(url);
        const errorMessage = extractErrorMessage(data, '请求失败');
        toast.error(`${apiPath}: ${errorMessage}`, { duration: 5000 });
        // 埋点：接口业务错误上报
        // 追加 engine_type / template_type / bot_kind 三个维度，
        // 供按 bot 种类（openclaw / personal_aicoding / normal_cc / app_coding / moltis / hermes）
        // 切分失败率，详见 docs/架构与规范/埋点/应用Bot埋点方案.md
        const scopeCtx = getCurrentBotKindContext();
        Tracert.click('c468663.d693662', {
          api_path: apiPath,
          error_msg: errorMessage,
          user_id:
            useUserStore.getState() /* spm: 接口-业务错误 */.userId || '',
          engine_type: scopeCtx.engine_type,
          template_type: scopeCtx.template_type,
          bot_kind: scopeCtx.bot_kind,
        });
      }
      return response;
    },
  ],
  errorConfig: {
    errorHandler(error: any, opts: any) {
      const status = error?.response?.status;
      const requestUrl = error?.config?.url || '未知接口';

      // 检测本地设备跨域错误（Chrome Private Network Access 拦截）
      // 必须放在 skipErrorHandler 判断之前：业务 hook（如 useConversations / useModels）
      // 大量使用 skipErrorHandler: true 抑制全局错误，会导致原本属于浏览器级强阻断的 PNA 失败
      // 也被静默吞掉。PNA 错误一旦发生意味着所有 /api 请求都会持续失败，必须强提示用户。
      // 触发条件：
      // 1. 非 Electron / VSCode 环境（这两端不会受 PNA 限制）
      // 2. 没有 response（请求未到达服务器）
      // 3. 错误是 Network Error / ERR_NETWORK
      // 4. 目标 URL 是本机或私有网段（PNA 覆盖范围）
      if (
        !isElectron() &&
        !isVSCode() &&
        isLocalNetworkBlocked(error, requestUrl)
      ) {
        showLocalNetworkBlockedPrompt();
        return;
      }

      if (opts?.skipErrorHandler) return;

      const responseData = error?.response?.data;

      if (status === 401) {
        // 401 由 loginPrompt 处理，不重复提示
        return;
      }

      // 提取后端返回的错误信息
      const backendMessage = responseData
        ? extractErrorMessage(responseData, '')
        : '';

      // 埋点：接口 HTTP 错误上报
      // 追加 engine_type / template_type / bot_kind 三个维度，详见
      // docs/架构与规范/埋点/应用Bot埋点方案.md
      const scopeCtxHttp = getCurrentBotKindContext();
      Tracert.click('c468663.d693663', {
        api_path: formatApiPath(requestUrl) /* spm: 接口-HTTP错误 */,
        status_code: status || 0,
        error_msg: backendMessage || error?.message || '未知错误',
        user_id: useUserStore.getState().userId || '',
        engine_type: scopeCtxHttp.engine_type,
        template_type: scopeCtxHttp.template_type,
        bot_kind: scopeCtxHttp.bot_kind,
      });

      // 使用统一的错误处理工具显示错误
      showRequestError(requestUrl, error, status, backendMessage);
    },
  },
};
