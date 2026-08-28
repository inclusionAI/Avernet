import type { BotRuntimeStage } from '@/domain/botWorkshop';
import { userScopedParams } from '../bots/botController';
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

export interface IamTokenResponse {
  iam_token: string;
}

export type BotIamTokenStage = BotRuntimeStage;

/** 获取当前登录用户的 IAM Token，用于 OpenClaw WebSocket 请求身份鉴权。 */
export async function getIamToken(userId?: string): Promise<string> {
  const response = await backendRequest<BackendApiEnvelope<IamTokenResponse>>('/openapi/v1/org/user/iam-token', {
    method: 'GET',
    params: userScopedParams(userId ? { user_id: userId } : {}),
  });
  const token = response.data?.iam_token?.trim();
  if (!token) {
    throw new Error(response.message || 'IAM 身份凭证获取失败');
  }
  return token;
}

/** 按 Bot 获取当前登录用户的 IAM Token，用于「对话协作」单聊 WebSocket 请求身份鉴权。 */
export async function getBotIamToken(
  botId: string,
  userId?: string,
  entityId?: string,
  stage: BotIamTokenStage = 'online',
): Promise<string> {
  const response = await backendRequest<BackendApiEnvelope<IamTokenResponse>>(
    `/openapi/v1/bots/${encodeURIComponent(botId)}/iam-token`,
    {
      method: 'POST',
      params: userScopedParams({
        ...(userId ? { user_id: userId } : {}),
        ...(entityId ? { entity_id: entityId } : {}),
        stage,
      }),
    },
  );
  const token = response.data?.iam_token?.trim();
  if (!token) {
    throw new Error(response.message || 'IAM 身份凭证获取失败');
  }
  return token;
}
