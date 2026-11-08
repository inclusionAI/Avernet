import type { CommandConfig, SubmitContext } from '@tc-chat/ui/es/Sender';
import type { ReactNode } from 'react';

/** Optional, platform-owned composer decoration. No platform data or protocol in Open Core. */
export interface ConversationComposerContext {
  botId: string;
  ownerId: string;
  sessionId: string;
  spaceId?: string;
  botType?: string;
  runtimeStage: 'draft' | 'verify' | 'online';
}
export interface ConversationComposerValue {
  command?: CommandConfig;
  header?: ReactNode;
  onSend: (content: string, context?: SubmitContext) => void;
}
export interface ConversationComposerProps extends ConversationComposerValue {
  context: ConversationComposerContext;
  children: (value: ConversationComposerValue) => ReactNode;
}
