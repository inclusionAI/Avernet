import { useIdentityStore } from '@/stores/identityStore';
import { extractFriendlyErrorMessage, formatApiPath } from '@/utils/requestErrorHandler';
import { retryOnTransient } from '@/utils/retryRequest';

export interface BackendRequestOptions {
  method?: string;
  params?: Record<string, unknown>;
  data?: unknown;
  rawBody?: BodyInit;
  headers?: Record<string, string>;
  retryOnTransient?: boolean;
  responseType?: 'json' | 'blob' | 'text';
  injectUserId?: boolean;
  signal?: AbortSignal;
}

export class BackendRequestError extends Error {
  status?: number;
  data?: unknown;
  apiPath: string;

  constructor(message: string, options: { status?: number; data?: unknown; apiPath: string }) {
    super(message);
    this.name = 'BackendRequestError';
    this.status = options.status;
    this.data = options.data;
    this.apiPath = options.apiPath;
  }
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
function injectUserId(
  url: string,
  params: Record<string, unknown> | undefined,
  data: unknown,
): Record<string, unknown> | undefined {
  if (!url.startsWith('/openapi') && !url.startsWith('/api/')) return params;
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
  const finalParams = options.injectUserId === false ? options.params : injectUserId(url, options.params, options.data);
  const requestUrl = withQuery(url, finalParams);
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
    throw new BackendRequestError(
      extractFriendlyErrorMessage({ response: { status: response.status, data: responseData } }),
      {
        status: response.status,
        data: responseData,
        apiPath,
      },
    );
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
