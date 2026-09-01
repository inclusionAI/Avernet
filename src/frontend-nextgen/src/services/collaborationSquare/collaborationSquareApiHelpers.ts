// @sdd: CollaborationSquareApiAdapter 的纯函数 helper（错误映射 / bot 名解析 / id 拆分）。
// 从 adapter 拆出以满足 Service≤300 行守卫；adapter 聚焦接口编排，helper 聚焦协议归一。
import { queryCollaborationBots } from '@/services/backendApi/collaboration/collaborationBotController';
import { PublicGroupCatalogError } from '@/services/backendApi/collaboration/collaborationGroupController';
import { PublicBotCatalogError } from '@/services/backendApi/collaboration/publicBotController';
import { AceLoginRedirectError, BackendRequestError } from '@/services/backendApi/httpClient';
import { CollaborationSquareError } from './collaborationSquareError';

export function unsupported(message: string): never {
  throw new CollaborationSquareError('unsupported', message);
}

/** httpClient 上游探测到 ACE 未登录体时会抛 AceLoginRedirectError;协作域通用 error mapper 需将其映射为 unauthenticated,
 *  而非被兜底吞成 network(否则既有 {code:'unauthenticated'} 断言失守,且丢失登录态语义)。 */
function isAceLoginRedirectError(error: unknown): error is AceLoginRedirectError {
  return (
    error instanceof AceLoginRedirectError ||
    (typeof error === 'object' && error !== null && (error as { name?: unknown }).name === 'AceLoginRedirectError')
  );
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

export { isAceLoginResponse } from '@/services/backendApi/aceLoginBody';

export function splitBotId(botId: string): { realBotId: string; ownerId?: string } {
  const separator = botId.indexOf(':');
  if (separator < 0) return { realBotId: botId };
  return { realBotId: botId.slice(0, separator), ownerId: botId.slice(separator + 1) || undefined };
}

export function mapListError(error: unknown, resource: 'Bot' | '协作群'): never {
  if (typeof error === 'object' && error !== null && 'name' in error && error.name === 'AbortError') throw error;
  if (error instanceof CollaborationSquareError) throw error;
  if (isAceLoginRedirectError(error)) {
    throw new CollaborationSquareError('unauthenticated', '登录状态已失效，请重新登录后重试');
  }
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

export function mapActionError(error: unknown, action: '申请好友权限' | '创建 Bot 会话' | '创建协作群会话'): never {
  if (typeof error === 'object' && error !== null && 'name' in error && error.name === 'AbortError') throw error;
  if (error instanceof CollaborationSquareError) throw error;
  if (isAceLoginRedirectError(error)) {
    throw new CollaborationSquareError('unauthenticated', '登录状态已失效，请重新登录后重试');
  }
  if (error instanceof BackendRequestError) {
    if (error.status === 401) throw new CollaborationSquareError('unauthenticated', '登录状态已失效，请重新登录后重试');
    if (error.status === 403) throw new CollaborationSquareError('forbidden', `当前账号无权${action}`);
    if (error.status === 409 && action === '创建协作群会话')
      throw new CollaborationSquareError('duplicate_action', '协作群状态已变化，请刷新后重试');
    if (error.status === 404) {
      const code = backendErrorCode(error);
      if (code === 'bot_not_found' && action === '申请好友权限') {
        throw new CollaborationSquareError('network', '目标 Bot 当前不可用，申请未提交，请稍后重试');
      }
      if (action === '创建协作群会话' && (code === 'group_not_found' || code === 'not_public')) {
        throw new CollaborationSquareError('target_invalid', '协作群已取消公开或不可访问');
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

const sessionRoleLabels: Record<string, string> = {
  consultant: '顾问',
  manager: '管理者',
  worker: '执行者',
  observer: '观察者',
  driver: '驱动者',
};

/** 将后端 session participant role 映射为中文展示标签，未知 role 原样返回。 */
export function mapSessionRole(role?: string): string | undefined {
  return role ? sessionRoleLabels[role] ?? role : undefined;
}

/**
 * 经 POST /openapi/v1/collaboration/bots/query 批量解析 bot/human 展示名，返回 {bot_id: name} 映射。
 * 后端返回的 bot 列表与请求 id 列表非一一对应（部分 id 查不到，如已删除），按 bot_id 匹配；
 * 失败返回空映射，调用方回退兜底文案。
 */
export async function resolveBotNames(botIds: string[]): Promise<Record<string, string>> {
  if (botIds.length === 0) return {};
  try {
    const resp = await queryCollaborationBots({ bot_ids: botIds });
    const items = resp.data?.items ?? [];
    const map: Record<string, string> = {};
    for (const item of items) {
      if (item.bot_id && item.name) map[item.bot_id] = item.name;
    }
    return map;
  } catch {
    return {};
  }
}
