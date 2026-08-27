import { backendRequest } from '../httpClient';

export interface PrivateChatApiResponse<T> {
  success: boolean;
  message?: string;
  error?: string;
  error_code?: number;
  data?: T;
}

export type PrivateChatConnectionType = 'local' | 'remote' | 'desktop';

export interface PrivateChatSessionConnection {
  type: PrivateChatConnectionType | string;
  target: string;
  token: string;
  engine_type: string;
  binding_id?: number;
  bind_id?: number;
}

export interface PrivateChatSession {
  session_key: string;
  is_new: boolean;
  connection: PrivateChatSessionConnection | null;
  collection?: PrivateChatSessionConnection | null;
  need_poll?: boolean;
}

export class PrivateChatSessionBusinessError extends Error {
  readonly errorCode?: number;
  readonly response: PrivateChatApiResponse<PrivateChatSession>;

  constructor(response: PrivateChatApiResponse<PrivateChatSession>) {
    super(response.message || response.error || '获取客服会话失败');
    this.name = 'PrivateChatSessionBusinessError';
    this.errorCode = response.error_code;
    this.response = response;
  }
}

/** 获取或创建与指定专家 Bot 的私聊 Session。 */
export async function getPrivateChatSession(botId: string, ownerId: string): Promise<PrivateChatSession> {
  const response = await backendRequest<PrivateChatApiResponse<PrivateChatSession>>(
    `/api/v1/expert-chats/${encodeURIComponent(botId)}/${encodeURIComponent(ownerId)}/session`,
    { method: 'POST' },
  );

  if (!response.success || !response.data) {
    throw new PrivateChatSessionBusinessError(response);
  }
  return response.data;
}
