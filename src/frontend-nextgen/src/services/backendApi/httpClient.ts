import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { useIdentityStore } from '@/stores/identityStore';
import { useLoginRedirectStore } from '@/stores/loginRedirectStore';
import { useLoginStrategyStore } from '@/stores/loginStrategyStore';
import { buildToastKey, extractFriendlyErrorMessage, formatApiPath } from '@/utils/requestErrorHandler';
import { retryOnTransient } from '@/utils/retryRequest';
import { extractLoginUrl, isAceLoginResponse } from './aceLoginBody';

export interface BackendRequestOptions {
  method?: string;
  params?: Record<string, unknown>;
  data?: unknown;
  rawBody?: BodyInit;
  headers?: Record<string, string>;
  retryOnTransient?: boolean;
  responseType?: 'json' | 'blob' | 'text';
  injectUserId?: boolean;
  /** 操作语义标签(如 create-skill),参与 toastKey 去重键组成,保证同接口跨操作的独立失败不被误合并。 */
  operation?: string;
  signal?: AbortSignal;
  /** 旧版 AgentCoding /api/** 接口的特殊后端；新版 /api/** 不设置此项。 */
  target?: 'default' | 'legacy-agentclaw';
}

export class BackendRequestError extends Error {
  status?: number;
  data?: unknown;
  apiPath: string;
  /** 去重键(传给下游 notifyError 作 sonner 稳定 id);协议层已投递默认提示时设置。 */
  toastKey?: string;
  /** 协议层默认提示是否已投递;Hook 守卫 helper(safeReportError)据此跳过自有 toast 防重复。 */
  alreadyHandled?: boolean;

  constructor(
    message: string,
    options: { status?: number; data?: unknown; apiPath: string; toastKey?: string; alreadyHandled?: boolean },
  ) {
    super(message);
    this.name = 'BackendRequestError';
    this.status = options.status;
    this.data = options.data;
    this.apiPath = options.apiPath;
    this.toastKey = options.toastKey;
    this.alreadyHandled = options.alreadyHandled;
  }
}

/**
 * 网关级 ACE 登录拦截体探测命中后抛出。携带网关下发的登录链接(若可从 body 取出);
 * 跳转信号已在探测点经 loginRedirectStore 登记单飞,本错误用于让 awaiting 调用方停止,
 * 不把登录体当作成功数据继续渲染(见 design.md D4)。面向用户信息即规范跳转文案。
 */
export class AceLoginRedirectError extends Error {
  loginUrl?: string;
  constructor(loginUrl?: string) {
    super('登录态失效，正在跳转登录…');
    this.name = 'AceLoginRedirectError';
    this.loginUrl = loginUrl;
  }
}

/**
 * 由探测点(httpClient 内部 + raw-fetch 旁路:sessionService/groupExecuteService/副屏面板)统一调用,
 * 登记单飞登录跳转信号。纯触发器——副作用(toast + 当前标签页跳转)由顶层观察者
 * useGatewayLoginRedirect 在 Hook 层消费 store 的 pendingLogin 完成(守 Service 禁 toast/DOM)。
 *
 * 收口在此而非让各旁路各自 import store:src/assets/** 副屏资产目录按 lint 规则禁止 import stores,
 * 只允许 import services,故把触发器收口在 service 层的 httpClient,旁路统一经此调用。
 */
export function triggerAceLoginRedirect(loginUrl?: string): void {
  if (!loginUrl) return;
  useLoginRedirectStore.getState().requestRedirect(loginUrl);
}

/**
 * 外部 `oauth-provider` 策略下登记弹窗提示信号（与 `triggerAceLoginRedirect` 对偶）。
 * 由 `httpClient` ACE 体探测（oauth 模式分支）/ raw-fetch 旁路 / `useExternalAuthGuard` 主动 401 调用，
 * 副作用（弹 `ExternalLoginPromptModal`）在 Hook/组件层消费 `pendingLogin{mode:'prompt'}`（守分层）。
 */
export function triggerLoginPrompt(): void {
  useLoginRedirectStore.getState().requestPrompt();
}

/**
 * 对 /openapi 和 /api/ 开头的请求自动注入 user_id query 参数。
 * 从 identityStore 动态读取（此处非 selector，允许 getState()）。
 * 如果 options.params 已显式传 user_id，或请求体（data）已携带 user_id
 * （部分 controller 在 body 内传 user_id），则不重复注入到 query，避免 body 与 query 同时出现 user_id。
 *
 * TODO(security): 注入对所有 /openapi + /api/ 生效，包括 admin/work-order/spaces 等 GET，
 * 这些请求被动带上了未预期的 user_id query；且 user_id 进入 URL，若后端以 query 而非会话
 * 鉴别身份则存在冒充风险。需按域收窄注入范围，或由 controller 显式传入身份。
 */
/**
 * legacy-agentclaw 只是告诉 dev server / Tern proxy 走旧 AgentClaw 路由。
 * 请求本身必须保持同源相对路径，不能在浏览器里直连 agentclaw-pre：
 * - 本地开发仍显示/发送同源的 /api/... 路径；
 * - 由 config.local.ts / internal runtime 的代理转发到真实后端。
 */
function resolveBackendUrl(url: string): string {
  return url;
}

function isApiRequestUrl(url: string): boolean {
  return url.startsWith('/openapi') || url.startsWith('/api/');
}

function injectUserId(
  url: string,
  params: Record<string, unknown> | undefined,
  data: unknown,
): Record<string, unknown> | undefined {
  if (!isApiRequestUrl(url)) return params;
  if (params?.user_id) return params;
  if (data && typeof data === 'object' && 'user_id' in data) return params;
  const userId = useIdentityStore.getState().currentIdentityId;
  if (!userId) return params;
  return { ...params, user_id: userId };
}

function withQuery(url: string, params?: Record<string, unknown>) {
  if (!params) return url;
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null) search.set(key, String(value));
  });
  const query = search.toString();
  return query ? `${url}?${query}` : url;
}

async function readResponseData(
  response: Response,
  responseType?: BackendRequestOptions['responseType'],
): Promise<unknown> {
  if (responseType === 'text') return response.text();
  if (responseType === 'blob') return response.blob();
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('application/json')) return response.json();
  return response.text();
}

async function executeBackendRequest<T>(url: string, options: BackendRequestOptions): Promise<T> {
  const resolvedUrl = resolveBackendUrl(url);
  const finalParams =
    options.injectUserId === false ? options.params : injectUserId(resolvedUrl, options.params, options.data);
  const requestUrl = withQuery(resolvedUrl, finalParams);
  const response = await fetch(requestUrl, {
    method: options.method ?? 'GET',
    headers: {
      ...(options.rawBody === undefined ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers,
    },
    body: options.rawBody ?? (options.data === undefined ? undefined : JSON.stringify(options.data)),
    credentials: 'include',
    signal: options.signal,
  });

  const responseData = await readResponseData(response, options.responseType);

  if (!response.ok) {
    const apiPath = formatApiPath(requestUrl);
    const message = extractFriendlyErrorMessage({ response: { status: response.status, data: responseData } });
    const operation = options.operation;
    const toastKey = buildToastKey({ apiPath, operation, message });
    // 默认提示投递(由顶层观察者 useErrorNotifyObserver 兜底发起):Service 层只 enqueue 上抛,不直接 toast,
    // 守 `src/services` 禁 toast/DOM。Hook 可在 catch 中 cancel(toastKey) 静默,或经 safeReportError 跳过重复。
    useErrorNotifyStore.getState().enqueue({ toastKey, message, apiPath, operation });
    throw new BackendRequestError(message, {
      status: response.status,
      data: responseData,
      apiPath,
      toastKey,
      alreadyHandled: true,
    });
  }

  // 网关级 ACE 登录拦截体(HTTP 2xx + 登录 body):登记单飞跳转信号 + 抛 AceLoginRedirectError,
  // 避免把登录体当作成功数据返回给无校验的调用方继续渲染。toast + window.location 由顶层观察者
  // useGatewayLoginRedirect 消费 store 的 pendingLogin 完成（redirect 模式；prompt 模式由 ExternalLoginPromptModal 消费）。
  if (isAceLoginResponse(responseData)) {
    const loginUrl = extractLoginUrl(responseData);
    // loginStrategy 分支：外部(oauth-provider)→弹窗信号(不携带 ACE pubLogin url，由 /auth/url 取 provider)；
    // 内部(ace-gateway)现状→硬跳转。两者都抛 AceLoginRedirectError 阻止 stale 渲染。
    if (useLoginStrategyStore.getState().loginStrategy === 'oauth-provider') {
      triggerLoginPrompt();
      // 外部模式不携带 ACE pubLogin url（provider url 由 /auth/url 取）；仍抛错阻止 stale 渲染。
      throw new AceLoginRedirectError();
    }
    triggerAceLoginRedirect(loginUrl);
    throw new AceLoginRedirectError(loginUrl);
  }

  return responseData as T;
}

// 后端接口统一通过该出口调用，禁止在 Controller 中硬编码内部域名。
export async function backendRequest<T>(url: string, options: BackendRequestOptions = {}): Promise<T> {
  const requestFactory = () => executeBackendRequest<T>(url, options);
  if (options.retryOnTransient && (options.method ?? 'GET').toUpperCase() === 'GET') {
    return retryOnTransient(requestFactory);
  }
  return requestFactory();
}
