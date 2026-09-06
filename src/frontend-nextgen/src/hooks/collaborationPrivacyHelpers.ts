import type { CollaborationBot, PublicAudience } from '@/domain/collaborationPrivacy/types';
import type { DirectSetting } from '@/services/collaborationPrivacy';

export interface Confirmation {
  bot: CollaborationBot;
  setting: DirectSetting;
  value: boolean | 'online' | 'hidden';
  title: string;
  description: string;
}

export interface PublicationEditorState {
  botId: string;
  audience: PublicAudience;
}

export type ScopeViewerState =
  | { kind: 'publication'; botId: string; audience: PublicAudience }
  | { kind: 'friendApproval'; botId: string };

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败，请稍后重试';
}

export function normalizeBotIdentityId(id: string): string {
  const separator = id.indexOf(':');
  return separator >= 0 ? id.slice(0, separator) : id;
}

export function matchesBotIdentity(botId: string, identityId: string): boolean {
  return botId === identityId || normalizeBotIdentityId(botId) === normalizeBotIdentityId(identityId);
}

export function directSettingLabel(setting: DirectSetting, value: Confirmation['value']): string {
  switch (setting) {
    case 'collaborationStatus':
      return value === 'online' ? '已开启参与协作群聊' : '已停止参与协作群聊';
    case 'profilePublic':
      return value ? '已公开 Bot 画像' : '已关闭 Bot 画像公开';
    case 'taskClaimingEnabled':
      return value ? '已开启任务认领' : '已关闭任务认领';
    case 'dreamModelEnabled':
      return value ? '已开启 Dream Mode' : '已关闭 Dream Mode';
  }
}

export function buildDirectConfirmation(
  bot: CollaborationBot,
  setting: DirectSetting,
  value: Confirmation['value'],
): Confirmation | null {
  if (setting !== 'collaborationStatus' && setting !== 'profilePublic') return null;
  const target =
    setting === 'collaborationStatus'
      ? value === 'online'
        ? '允许参与协作群聊'
        : '停止参与协作群聊'
      : value
      ? '公开 Bot 画像'
      : '关闭 Bot 画像公开';
  const description =
    setting === 'collaborationStatus'
      ? value === 'online'
        ? `${bot.name} 开启后可加入新协作群，并在已加入的协作群会话中回复消息。好友单聊不受影响。`
        : `${bot.name} 关闭后无法加入新协作群，并停止在已加入的协作群会话中回复消息。好友单聊不受影响。`
      : `${bot.name} 的画像公开状态将影响发现、推荐与协作匹配。`;
  return { bot, setting, value, title: `确认${target}？`, description };
}
