// @sdd: IdentityView → Identity 映射（从 useWorkspace 抽出,降低 Hook 体积 + 语义归类至映射层）。
import type { IdentityView } from '@/domain/collaboration';
import { resolveAuthenticatedDisplayName } from '@/domain/userIdentity';
import type { ChatBotView } from '@/services/workspace';
import type { ConversationTarget, Identity } from '@/services/workspace/workspaceModel';

export function mapIdentityViewToIdentity(view: IdentityView): Identity {
  return {
    id: view.id,
    name: view.displayName,
    kind: view.kind,
    avatar: view.avatarUrl ?? view.displayName.slice(0, 1),
    status: view.online ? 'available' : 'unavailable',
    chatStatus: view.status,
    reachability: view.reachability,
    engine: view.engine,
    botType: view.botType,
  };
}

/**
 * @sdd: IdentityView[] → 展示用 Identity[]（含登录用户名覆盖）。
 * 与 WorkspaceIdentitySwitcher 的名称覆盖口径一致：preferAuthenticatedUserProfile 开启且
 * 用户身份项 id 匹配登录用户时，用登录用户显示名替换 mine 返回名，保证各入口身份名一致。
 * 模块级身份选择器（如协作广场公开Bot Tab）复用本函数，不再各自内联覆盖逻辑。
 */
export function mapIdentityViewsToDisplayIdentities(
  views: IdentityView[],
  authenticatedUser: { userId: string; name: string } | null,
  preferAuthenticatedUserProfile: boolean,
): Identity[] {
  return views.map((view) => {
    const identity = mapIdentityViewToIdentity(view);
    if (!preferAuthenticatedUserProfile || identity.kind !== 'user') return identity;
    const name = resolveAuthenticatedDisplayName(
      { id: identity.id, kind: identity.kind, name: identity.name },
      authenticatedUser,
    );
    return name && name !== identity.name ? { ...identity, name } : identity;
  });
}

// @sdd: ChatBotView → ConversationTarget 映射（从 useWorkspace 抽出,降低 Hook 体积 + 语义归类至映射层）。
// v1.4：会话消息条数从列表副行上移至顶栏 summary（列表副行弱化为非主要信息，单聊/群聊会话行统一紧凑单行）。
export function findSelectedChatBot(
  bots: ChatBotView[],
  selectedSessionBotId: string | null | undefined,
  expandedBotIds: Record<string, unknown>,
): ChatBotView | null {
  const botId = selectedSessionBotId ?? Object.keys(expandedBotIds)[0] ?? null;
  return botId ? bots.find((bot) => bot.botId === botId) ?? null : null;
}

export function buildBotChatTarget(bot: ChatBotView, session?: { messageCount: number } | null): ConversationTarget {
  const base = `与 ${bot.displayName} 单聊`;
  return {
    id: bot.botId,
    name: bot.displayName,
    avatar: bot.avatarUrl ?? bot.displayName.slice(0, 1),
    engine: 'OpenClaw',
    status: bot.online ? 'available' : 'unavailable',
    summary: session && session.messageCount > 0 ? `${base} · ${session.messageCount} 条消息` : base,
    kind: 'single',
  };
}
