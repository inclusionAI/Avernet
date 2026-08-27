/**
 * GroupChatProvider 纯函数辅助——WS URL 构造、human 身份标识格式化、群聊 ws 帧 payload 装配。
 * 从 groupChatProvider.ts 拆出，便于独立单测并控制 Provider 文件体积。
 */
import type { GroupChatInput } from '@tc-chat/adapters';
import { resolveUserId } from './botSessionService';
import type { GroupChatRequest } from './groupChatProvider';

/** human 身份的 WS `bot_uuid`：mine 返回的 human bot_id 已带 human 前缀则原样使用，否则补前缀。 */
export function toHumanBotUuid(identityId: string): string {
  return /^human[_-]/.test(identityId) ? identityId : `human_${identityId}`;
}

/**
 * 构造群聊 ws 帧 payload（GroupChatInput）——字段保真透传（根因 C，对齐 open-claw useGroupChat.ts:846-862
 * + SDK buildBCSChatRequest 期望）：
 *   - query ← params.content（桥路径顶层 content 由 buildRequestParams 产；直接路径 send 顶层 content）。
 *   - botUuid 优先调用方传入（桥 @指定 bot），缺省 toHumanBotUuid(identityId) 兜底（直接路径不回归）。
 *   - mentionAll/replyToMessageId/panelContext/isInject 桥 extra 透传（GroupChatInput 已声明此五字段）。
 */
export function buildGroupChatPayload(params: GroupChatRequest, groupId: string, identityId: string): GroupChatInput {
  const payload: GroupChatInput = {
    query: params.content,
    groupId,
    senderId: resolveUserId(identityId),
    botUuid: params.botUuid ?? toHumanBotUuid(identityId),
  };
  if (params.mentions && params.mentions.length > 0) payload.mentions = params.mentions;
  if (params.mentionAll) payload.mentionAll = params.mentionAll;
  if (params.replyToMessageId) payload.replyToMessageId = params.replyToMessageId;
  if (params.attachments && params.attachments.length > 0) {
    payload.attachments = params.attachments as unknown as GroupChatInput['attachments'];
  }
  if (params.panelContext !== undefined) payload.panelContext = params.panelContext;
  if (params.isInject !== undefined) payload.isInject = params.isInject;
  return payload;
}

interface LocationLike {
  protocol: string;
  host: string;
}

/**
 * 构造协作群消息 WebSocket URL：同源 ws(s) + token 查询参数。
 * 不写死域名——protocol/host 取自 locationLike 或 window.location。
 */
export function buildGroupWsUrl({
  token,
  locationLike,
  wsOrigin,
}: {
  token: string;
  locationLike?: LocationLike;
  wsOrigin?: string;
}): string {
  if (wsOrigin) {
    return `${wsOrigin}/openapi/v1/collaboration/messages/ws?token=${encodeURIComponent(token)}`;
  }
  const location = locationLike ?? (typeof window !== 'undefined' ? (window.location as LocationLike) : undefined);
  const protocol = location?.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = location?.host || 'localhost';
  return `${protocol}//${host}/openapi/v1/collaboration/messages/ws?token=${encodeURIComponent(token)}`;
}

/**
 * 部署态 WebSocket 直连网关 origin 解析。
 *
 * 背景：部分宿主的 HTTP 代理不拦截 WebSocket 连接（`new WebSocket(url)` 不走 fetch 通道）。
 * 本地 dev 使用 dev-server proxy；部署态可由构建配置注入网关 origin。
 *
 * 策略：
 * - 本地开发（localhost / 127.0.0.1）：返回 undefined，`buildGroupWsUrl` 退回同源拼接，走 dev-server proxy。
 * - 部署态：根据公开的 `TEAMCLAW_DEV_ENV` 构建变量选取 PRE/PROD 网关，不依赖宿主域名规则。
 */
export function resolveGroupGatewayOrigin(hostname?: string): string | undefined {
  const host = hostname ?? (typeof window !== 'undefined' ? window.location.hostname : '');
  // 本地开发：dev-server proxy（ws:true）处理 WebSocket Upgrade，保持同源
  if (!host || host === 'localhost' || host === '127.0.0.1') return undefined;
  // 部署态：直连网关绕过不支持 Upgrade 的 HTTP 代理。
  const env = typeof TEAMCLAW_DEV_ENV !== 'undefined' ? TEAMCLAW_DEV_ENV : undefined;
  const isProd = env ? env === 'PROD' : !host.includes('-pre') && !host.includes('-dev');
  // typeof 守卫：jest / SSR 等未注入 define 的环境不会 ReferenceError
  const gwPre = typeof TEAMCLAW_WS_GW_PRE !== 'undefined' ? TEAMCLAW_WS_GW_PRE : '';
  const gwProd = typeof TEAMCLAW_WS_GW_PROD !== 'undefined' ? TEAMCLAW_WS_GW_PROD : '';
  const gwHttp = isProd ? gwProd : gwPre;
  if (!gwHttp) return undefined;
  return gwHttp;
}

/** 部署态 WebSocket 直连网关 origin（wss://），本地开发返回 undefined。 */
export function resolveGroupWsOrigin(hostname?: string): string | undefined {
  return resolveGroupGatewayOrigin(hostname)?.replace(/^https/, 'wss');
}
