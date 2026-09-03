import { isEnvelopeSuccessAnyDialect, type BackendApiEnvelope } from '@/services/backendApi/types';
import { request } from '@umijs/max';

export interface AuthEndpointConfig {
  providers: string;
  currentUser: string;
  refresh: string;
  logout: string;
}

/**
 * BCS compatibility endpoints. Keep this protocol boundary centralized so the
 * later Gateway OpenAPI migration changes no Component, Hook, or Service.
 *
 * 前缀为 BCS bcs-api-http 的公开挂载路径(`v1/openapi/mod.rs` `nest("/openapi/v1/auth")`,
 * 网关 domains `auth→bcs`)——阿里云部署实测生效;裸 `/auth/*` 仅 BCS 内网直连存在。
 * 注:路径以 `/openapi` 开头但走 umi `request` 通道,不经 `httpClient` 的 `injectUserId`
 * (若将来切 httpClient 通道,必须显式豁免该前缀,见 spec「不经业务身份注入」Requirement)。
 */
export const AUTH_ENDPOINTS: AuthEndpointConfig = {
  providers: '/openapi/v1/auth/url',
  currentUser: '/openapi/v1/auth/user',
  refresh: '/openapi/v1/auth/refresh',
  logout: '/openapi/v1/auth/logout',
};

export interface AuthProviderUrlDto {
  name: string;
  url: string;
}

export interface AuthProvidersDto {
  providers: AuthProviderUrlDto[];
}

export interface AuthUserDto {
  user_id: string;
  name?: string | null;
  provider: string;
  avatar?: string | null;
}

const cookieRequest = (method: 'GET' | 'POST') => ({
  method,
  credentials: 'include' as const,
  skipErrorHandler: true,
});

/**
 * 判断响应体是否为统一信封形状 `{code,message,data,request_id}`。
 * 只认形状不认成败:BCS(阿里云)auth 面成功为 `{code:20000,...}`;裸 DTO(本地 mock/未包信封部署)
 * 无 `code`/`data` 字段 → 非信封,原样透传。401 等 HTTP 层失败已在 request reject,不会走到此处。
 */
function hasEnvelopeShape(body: unknown): body is BackendApiEnvelope<unknown> {
  return typeof body === 'object' && body !== null && 'code' in body && 'data' in body;
}

/**
 * auth 协议边界解包(见 add-external-oauth-login 决策 9):
 * - 信封形状 + 双方言 2xx 成功段(经 isEnvelopeSuccessAnyDialect:python 6 位 / BCS 5 位任一) → 返回 `data`;
 * - 信封形状但 code 非 2xx 段(HTTP 200 异常形态,如网关误包 40100) → 按业务失败 reject;
 * - 非信封形状(裸 DTO) → 原样透传。
 *
 * 不用全局 `teamclawResponseInterceptor` 解包:auth 请求 `skipErrorHandler`,拦截器不介入;
 * 且信封 5 位码方言的全局判定属独立 change,此处自治,避免与登录链路耦合。
 */
function unwrapAuthEnvelope<T>(body: unknown): T {
  if (!hasEnvelopeShape(body)) return body as T;
  if (!isEnvelopeSuccessAnyDialect(body)) {
    const env = body as BackendApiEnvelope<unknown>;
    throw new Error(env.message || '认证接口返回业务失败');
  }
  return (body as BackendApiEnvelope<T>).data as T;
}

export function getAuthProviders() {
  return request<AuthProvidersDto>(AUTH_ENDPOINTS.providers, cookieRequest('GET')).then(
    unwrapAuthEnvelope<AuthProvidersDto>,
  );
}

export function getCurrentAuthUser() {
  return request<AuthUserDto>(AUTH_ENDPOINTS.currentUser, cookieRequest('GET')).then(unwrapAuthEnvelope<AuthUserDto>);
}

export function refreshAuthSession() {
  return request<void>(AUTH_ENDPOINTS.refresh, cookieRequest('POST')).then(unwrapAuthEnvelope<void>);
}

export function logoutAuthSession() {
  return request<void>(AUTH_ENDPOINTS.logout, cookieRequest('POST')).then(unwrapAuthEnvelope<void>);
}
