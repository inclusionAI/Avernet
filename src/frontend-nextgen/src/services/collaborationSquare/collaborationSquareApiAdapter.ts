import { mapPublicBotCatalogDto, mapPublicGroupCatalogDto } from '@/domain/collaborationSquare/mapper';
import type {
  CreateSessionResult,
  FriendRequestResult,
  HumanBotActionContext,
  OpenBotConversationResult,
  PublicBot,
  PublicBotDiscoveryQuery,
  PublicBotProfile,
  PublicBotSearchQuery,
  PublicGroup,
  PublicGroupMember,
  PublicGroupSearchQuery,
} from '@/domain/collaborationSquare/types';
import { createBotSession } from '@/services/backendApi/bots/privateBotSessionController';
import { createFriendConnectionRequest } from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import {
  listPublicGroups,
  PublicGroupCatalogError,
} from '@/services/backendApi/collaboration/collaborationGroupController';
import {
  discoverPublicBots,
  PublicBotCatalogError,
  searchPublicBots,
} from '@/services/backendApi/collaboration/publicBotController';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import { CollaborationSquareError } from './collaborationSquareError';
import type { CollaborationSquareGateway } from './collaborationSquareGateway';
import { listFriendBotRelationships } from './friendConnectionRelationships';

function unsupported(message: string): never {
  throw new CollaborationSquareError('unsupported', message);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isAceLoginResponse(value: unknown) {
  return isRecord(value) && value.actionType === 'LOGIN' && value.buserviceErrorCode === 'USER_NOT_LOGIN';
}

function mapListError(error: unknown, resource: 'Bot' | '协作群'): never {
  if (typeof error === 'object' && error !== null && 'name' in error && error.name === 'AbortError') throw error;
  if (error instanceof CollaborationSquareError) throw error;
  if (error instanceof PublicBotCatalogError || error instanceof PublicGroupCatalogError) {
    throw new CollaborationSquareError(
      error.code,
      error.code === 'unauthenticated' ? '登录状态已失效，请重新登录后重试' : `公开${resource}接口返回了无法识别的数据`,
    );
  }
  if (error instanceof BackendRequestError) {
    if (error.status === 401) throw new CollaborationSquareError('unauthenticated', '登录状态已失效，请重新登录后重试');
    if (error.status === 403) throw new CollaborationSquareError('forbidden', `当前账号无权访问公开${resource}`);
    throw new CollaborationSquareError('network', `公开${resource}加载失败，请稍后重试`);
  }
  throw new CollaborationSquareError('network', `公开${resource}加载失败，请稍后重试`);
}

function backendErrorCode(error: BackendRequestError): string | undefined {
  const candidates = [error.data, isRecord(error.data) ? error.data.data : undefined];
  for (const candidate of candidates) {
    if (!isRecord(candidate)) continue;
    if (typeof candidate.error_code === 'string') return candidate.error_code;
    if (typeof candidate.code === 'string') return candidate.code;
  }
  return undefined;
}

function mapActionError(error: unknown, action: '申请好友权限' | '创建 Bot 会话'): never {
  if (typeof error === 'object' && error !== null && 'name' in error && error.name === 'AbortError') throw error;
  if (error instanceof CollaborationSquareError) throw error;
  if (error instanceof BackendRequestError) {
    if (error.status === 401) throw new CollaborationSquareError('unauthenticated', '登录状态已失效，请重新登录后重试');
    if (error.status === 403) throw new CollaborationSquareError('forbidden', `当前账号无权${action}`);
    if (error.status === 404) {
      const code = backendErrorCode(error);
      if (code === 'bot_not_found' && action === '申请好友权限') {
        throw new CollaborationSquareError('network', '目标 Bot 当前不可用，申请未提交，请稍后重试');
      }
      if (
        code === 'target_invalid' ||
        code === 'not_public' ||
        code === 'bot_deleted' ||
        (action === '创建 Bot 会话' && (code === 'bot_not_found' || !code))
      ) {
        throw new CollaborationSquareError('target_invalid', '内容已取消公开或不可访问');
      }
    }
  }
  throw new CollaborationSquareError('network', `${action}失败，请稍后重试`);
}

function splitBotId(botId: string): { realBotId: string; ownerId?: string } {
  const separator = botId.indexOf(':');
  if (separator < 0) return { realBotId: botId };
  return { realBotId: botId.slice(0, separator), ownerId: botId.slice(separator + 1) || undefined };
}

async function enrichRelationships(bots: PublicBot[], context?: HumanBotActionContext, signal?: AbortSignal) {
  if (!context) return bots;
  const { friendIds, applyingIds } = await listFriendBotRelationships(context, signal);
  return bots.map((bot) => ({
    ...bot,
    relationshipStatus: friendIds.has(bot.id)
      ? ('friend' as const)
      : applyingIds.has(bot.id)
      ? ('applying' as const)
      : bot.relationshipStatus,
  }));
}

/** 接入公开 Bot Search/Discovery、Human→Bot 关系/会话与公开群列表；画像和群写能力保持显式 unsupported。 */
export class CollaborationSquareApiAdapter implements CollaborationSquareGateway {
  async listBots(
    query: PublicBotSearchQuery = {},
    context?: HumanBotActionContext,
    signal?: AbortSignal,
  ): Promise<PublicBot[]> {
    try {
      const response = await searchPublicBots(
        {
          ...(query.search?.trim() ? { search: query.search.trim() } : {}),
          page: query.page ?? 1,
          page_size: query.pageSize ?? 20,
        },
        signal,
      );
      const bots = response.data.items.flatMap((item) => {
        const bot = mapPublicBotCatalogDto(item);
        return bot ? [bot] : [];
      });
      return await enrichRelationships(bots, context, signal);
    } catch (error) {
      return mapListError(error, 'Bot');
    }
  }

  async discoverBots(
    query: PublicBotDiscoveryQuery,
    context?: HumanBotActionContext,
    signal?: AbortSignal,
  ): Promise<PublicBot[]> {
    try {
      const response = await discoverPublicBots(
        {
          keyword: query.keyword.trim(),
          top_k: query.topK ?? 20,
          min_score: query.minScore ?? 0.1,
          runtime_state: query.runtimeState ?? 'online',
        },
        signal,
      );
      const bots = response.data.items.flatMap((item) => {
        const bot = mapPublicBotCatalogDto(item);
        return bot ? [bot] : [];
      });
      return await enrichRelationships(bots, context, signal);
    } catch (error) {
      return mapListError(error, 'Bot');
    }
  }

  async getBotProfile(botId: string): Promise<PublicBotProfile> {
    void botId;
    return unsupported('Bot 画像接口尚未接入');
  }

  async requestBotFriendship(botId: string, context: HumanBotActionContext): Promise<FriendRequestResult> {
    void context.actorId;
    try {
      const response = await createFriendConnectionRequest({ to_actor: { type: 'bot', id: botId } });
      if (isAceLoginResponse(response))
        throw new CollaborationSquareError('unauthenticated', '登录状态已失效，请重新登录后重试');
      if (response.code !== 20100)
        throw new CollaborationSquareError('protocol_error', '好友申请接口返回了无法识别的业务码');
      const status = response.data?.status?.trim().toLowerCase();
      if (status === 'pending') return { status: 'applying' };
      if (status === 'approved') return { status: 'friend' };
      if (status === 'public_no_edge') return { status: 'none' };
      throw new CollaborationSquareError('protocol_error', '好友申请接口返回了无法识别的状态');
    } catch (error) {
      return mapActionError(error, '申请好友权限');
    }
  }

  async openBotConversation(botId: string, context: HumanBotActionContext): Promise<OpenBotConversationResult> {
    try {
      const { realBotId, ownerId } = splitBotId(botId);
      if (!realBotId || !context.userId.trim())
        throw new CollaborationSquareError('protocol_error', '创建 Bot 会话所需身份信息不完整');
      const response = await createBotSession(
        realBotId,
        {
          user_id: context.userId,
          ...(ownerId ? { owner_id: ownerId } : {}),
        },
        {},
      );
      if (isAceLoginResponse(response))
        throw new CollaborationSquareError('unauthenticated', '登录状态已失效，请重新登录后重试');
      if (response.code !== 200000)
        throw new CollaborationSquareError('protocol_error', '创建 Bot 会话接口返回了无法识别的业务码');
      const sessionId = response.data?.session_id;
      if (typeof sessionId !== 'string' || !sessionId.trim()) {
        throw new CollaborationSquareError('protocol_error', '创建 Bot 会话接口未返回 session_id');
      }
      return { sessionId };
    } catch (error) {
      return mapActionError(error, '创建 Bot 会话');
    }
  }

  async listGroups(query: PublicGroupSearchQuery = {}, signal?: AbortSignal): Promise<PublicGroup[]> {
    try {
      const response = await listPublicGroups(
        {
          ...(query.search?.trim() ? { q: query.search.trim() } : {}),
          offset: query.offset ?? 0,
          limit: query.limit ?? 20,
        },
        signal,
      );
      return response.data.items.flatMap((item) => {
        const group = mapPublicGroupCatalogDto(item);
        return group ? [group] : [];
      });
    } catch (error) {
      return mapListError(error, '协作群');
    }
  }

  async listGroupMembers(groupId: string): Promise<PublicGroupMember[]> {
    void groupId;
    return unsupported('公开协作群成员接口尚未接入');
  }

  async createGroupSession(groupId: string): Promise<CreateSessionResult> {
    void groupId;
    return unsupported('公开协作群会话接口尚未接入');
  }
}

export const collaborationSquareApiAdapter = new CollaborationSquareApiAdapter();
