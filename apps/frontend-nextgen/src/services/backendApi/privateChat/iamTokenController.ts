import type { BotRuntimeStage } from '@/domain/botWorkshop';
import { userScopedParams } from '../bots/botController';
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

export interface IamTokenResponse {
  iam_token: string;
}

export type BotIamTokenStage = BotRuntimeStage;

export interface BotIamTokenChatContext {
  sessionKey: string;
  /** 好友会话由 OpenAPI 校验归属后再把 session_id 转成内部 session_key。 */
  friendUserId?: string;
  ownerId?: string;
  /** 多实例服务 Bot 的选中实例:后端据此把 Caller 身份签发到选中实例
   * (openspec add-servicebot-iam-device-uuid)。门控=非 teclaw + 已选实例,
   * 不按运行态/Caller 模式门控。编排层的注入条件见 botChatProvider.fetchIamToken。 */
  deviceUuid?: string;
}

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
  stage?: BotIamTokenStage,
  chatContext?: BotIamTokenChatContext,
): Promise<string> {
  const response = await backendRequest<BackendApiEnvelope<IamTokenResponse>>(
    `/openapi/v1/bots/${encodeURIComponent(botId)}/iam-token`,
    {
      method: 'POST',
      params: userScopedParams({
        ...(userId ? { user_id: userId } : {}),
        ...(entityId ? { entity_id: entityId } : {}),
        ...(stage ? { stage } : {}),
        ...(chatContext?.deviceUuid ? { device_uuid: chatContext.deviceUuid } : {}),
        ...(chatContext?.friendUserId
          ? {
              owner_id: chatContext.ownerId ?? entityId,
              f_user_id: chatContext.friendUserId,
              session_id: chatContext.sessionKey,
            }
          : chatContext?.sessionKey
          ? { session_key: chatContext.sessionKey }
          : {}),
      }),
    },
  );
  const token = response.data?.iam_token?.trim();
  if (!token) {
    throw new Error(response.message || 'IAM 身份凭证获取失败');
  }
  return token;
}
