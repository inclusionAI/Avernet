// @sdd: IdentityView → Identity 映射（从 useWorkspace 抽出,降低 Hook 体积 + 语义归类至映射层）。
import type { IdentityView } from '@/domain/collaboration';
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
  };
}

// @sdd: ChatBotView → ConversationTarget 映射（从 useWorkspace 抽出,降低 Hook 体积 + 语义归类至映射层）。
export function buildBotChatTarget(bot: ChatBotView): ConversationTarget {
  return {
    id: bot.botId,
    name: bot.displayName,
    avatar: bot.avatarUrl ?? bot.displayName.slice(0, 1),
    engine: 'OpenClaw',
    status: bot.online ? 'available' : 'unavailable',
    summary: `与 ${bot.displayName} 单聊`,
    kind: 'single',
  };
}
