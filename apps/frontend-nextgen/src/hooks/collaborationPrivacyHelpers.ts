import type { CollaborationBot, PublicAudience, PublicConfig } from '@/domain/collaborationPrivacy/types';
import type { DirectSetting, PublicationCommand } from '@/services/collaborationPrivacy';

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

export function publicationSuccessMessage(config: PublicConfig): string {
  return config.scope === 'none'
    ? 'Bot 可见性已更新为不可见，当前立即生效'
    : '可见性变更申请已提交，当前可见性保持不变';
}

/** 外部 Bot 保存即生效（PATCH 直改无审批），成功文案不出现审批话术。 */
export const externalPublicationSaveMessage = 'Bot 可见性已保存，当前立即生效';

export interface PublicationCommandInput {
  botId: string;
  audience: PublicAudience;
  config: PublicConfig;
  deptEntries?: Array<{ deptNo: string; deptName: string }>;
  /** 仅由外部 Bot 通用配置入口显式置 true（不按 Bot ID 猜测）；TC 命令保持原形态不携带该字段。 */
  isExternalBot?: boolean;
}

/** 组装可见性提交指令：外部 Bot 走 PATCH 直改路由，TC 走既有 publish 审批路由。 */
export function buildPublicationCommand(input: PublicationCommandInput): PublicationCommand {
  const { isExternalBot, ...command } = input;
  return isExternalBot ? { ...command, isExternalBot } : command;
}

export function directSettingLabel(setting: DirectSetting, value: Confirmation['value']): string {
  switch (setting) {
    case 'collaborationStatus':
      return value === 'online' ? '已开启参与协作群聊' : '已停止参与协作群聊';
    case 'profilePublic':
      return value ? '已公开 Bot 画像' : '已停止公开 Bot 画像';
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
      : '停止公开 Bot 画像';
  const description =
    setting === 'collaborationStatus'
      ? value === 'online'
        ? `${bot.name} 开启后可加入新协作群，并在已加入的协作群会话中回复消息。好友单聊不受影响。`
        : `${bot.name} 关闭后无法加入新协作群，并停止在已加入的协作群会话中回复消息。好友单聊不受影响。`
      : `${bot.name} 的画像公开状态将影响发现、推荐与协作匹配。`;
  return { bot, setting, value, title: `确认${target}？`, description };
}
