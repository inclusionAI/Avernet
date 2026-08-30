import { backendRequest } from '../httpClient';
import type { PrivateChatConnectionType, PrivateChatSessionConnection } from './privateChatController';

export interface PrivateSessionRawMessage {
  id?: string;
  role?: string;
  content?: unknown;
  blocks?: Array<Record<string, unknown>>;
  metadata?: Record<string, unknown>;
  history_meta?: Record<string, unknown>;
  gmt_created?: string;
  created_at?: string;
  createdAt?: string | number;
  timestamp?: string | number;
  [key: string]: unknown;
}

export interface PrivateSessionMessagesResponse {
  success: boolean;
  data?: PrivateSessionRawMessage[];
  total?: number;
  message?: string;
  error?: string;
}

export function encodeToUrlSafeBase64(value: string): string {
  const bytes = new TextEncoder().encode(value);
  let binary = '';
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function normalizeConnectionType(type: string): PrivateChatConnectionType {
  return type === 'local' || type === 'desktop' ? type : 'remote';
}

export function buildPrivateSessionHttpRequest(
  path: string,
  connection: PrivateChatSessionConnection,
): { url: string; headers: Record<string, string> } {
  const type = normalizeConnectionType(connection.type);
  if (type === 'local' || type === 'desktop') {
    return {
      url: `http://${connection.target}${path}`,
      headers: connection.token ? { Authorization: `Bearer ${connection.token}` } : {},
    };
  }

  return {
    url: `/proxypass/${connection.target}${path}`,
    headers: connection.token ? { 'X-PROXYPASS-TOKEN': connection.token } : {},
  };
}

export async function getPrivateSessionMessages(
  sessionKey: string,
  connection: PrivateChatSessionConnection,
  params: { limit?: number; offset?: number } = {},
): Promise<PrivateSessionRawMessage[]> {
  const encodedSessionKey = encodeToUrlSafeBase64(sessionKey);
  const request = buildPrivateSessionHttpRequest(`/api/sessions/${encodedSessionKey}/messages`, connection);
  const response = await backendRequest<PrivateSessionMessagesResponse>(request.url, {
    method: 'GET',
    headers: request.headers,
    params: {
      limit: params.limit ?? 1000,
      offset: params.offset ?? 0,
    },
    retryOnTransient: true,
  });

  if (!response.success) {
    throw new Error(response.message || response.error || '查询客服历史消息失败');
  }
  return Array.isArray(response.data) ? response.data : [];
}
