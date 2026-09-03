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
