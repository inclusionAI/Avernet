// Conversation URL route contract 的纯函数 parse/serialize。
// 规范形式(docs/specs/2026-09-24-workspace-conversation-navigation-refactor.md AC-13):
//   managed/mine:   section=managed&bot={bot}&origin=mine[&scope=favorite][&session={session}]
//   managed/others: section=managed&bot={bot}&origin=others&friend={friend}[&session={session}]
//   friend Bot:     section=friend&bot={bot}[&session={session}]
// 不依赖 React / Store / Router。
import type {
  ConversationBotSection,
  ConversationOrigin,
  ConversationRouteState,
  ConversationSessionScope,
} from './types';

const SECTIONS: readonly ConversationBotSection[] = ['managed', 'friend'];
const ORIGINS: readonly ConversationOrigin[] = ['mine', 'others'];
const SCOPES: readonly ConversationSessionScope[] = ['all', 'favorite'];

function toEnumValue<T extends string>(candidates: readonly T[], raw: string | null): T | undefined {
  return candidates.find((candidate) => candidate === raw);
}

/** 空串归一化为 undefined;其余原样保留。 */
function nonEmpty(value: string | null): string | undefined {
  return value || undefined;
}

export function parseConversationRoute(input: URLSearchParams | string): ConversationRouteState {
  const params = typeof input === 'string' ? new URLSearchParams(input) : input;
  const section = toEnumValue(SECTIONS, params.get('section'));
  const botId = nonEmpty(params.get('bot'));
  let origin = toEnumValue(ORIGINS, params.get('origin'));
  let scope = toEnumValue(SCOPES, params.get('scope'));
  let friendUserId = nonEmpty(params.get('friend'));
  const sessionId = nonEmpty(params.get('session'));

  // 非法组合归一化:others 移除 scope(强制按 all 读取);
  // friend section 移除 managed-only 字段(origin/scope/friend);
  // section 缺省时保持未解析,由 Hook 的目录匹配推导。
  if (origin === 'others') scope = undefined;
  if (section === 'friend') {
    origin = undefined;
    scope = undefined;
    friendUserId = undefined;
  }

  return { section, botId, origin, scope, friendUserId, sessionId };
}

export function serializeConversationRoute(route: ConversationRouteState): string {
  const params = new URLSearchParams();
  if (route.section === 'friend') {
    params.set('section', 'friend');
    if (route.botId) params.set('bot', route.botId);
    if (route.sessionId) params.set('session', route.sessionId);
    return params.toString();
  }
  if (route.section === 'managed') {
    const origin: ConversationOrigin = route.origin ?? 'mine';
    params.set('section', 'managed');
    if (route.botId) params.set('bot', route.botId);
    params.set('origin', origin);
    if (origin === 'others') {
      if (route.friendUserId) params.set('friend', route.friendUserId);
    } else if (route.scope === 'favorite') {
      params.set('scope', route.scope);
    }
    if (route.sessionId) params.set('session', route.sessionId);
    return params.toString();
  }
  // section 未解析:仅透出 bot/session,不猜测 section。
  if (route.botId) params.set('bot', route.botId);
  if (route.sessionId) params.set('session', route.sessionId);
  return params.toString();
}
