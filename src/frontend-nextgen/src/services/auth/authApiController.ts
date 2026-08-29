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
 */
export const AUTH_ENDPOINTS: AuthEndpointConfig = {
  providers: '/auth/url',
  currentUser: '/auth/user',
  refresh: '/auth/refresh',
  logout: '/auth/logout',
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

export function getAuthProviders() {
  return request<AuthProvidersDto>(AUTH_ENDPOINTS.providers, cookieRequest('GET'));
}

export function getCurrentAuthUser() {
  return request<AuthUserDto>(AUTH_ENDPOINTS.currentUser, cookieRequest('GET'));
}

export function refreshAuthSession() {
  return request<void>(AUTH_ENDPOINTS.refresh, cookieRequest('POST'));
}

export function logoutAuthSession() {
  return request<void>(AUTH_ENDPOINTS.logout, cookieRequest('POST'));
}
