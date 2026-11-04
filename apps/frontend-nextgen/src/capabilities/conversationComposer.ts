import type { CommandConfig, SubmitContext } from '@tc-chat/ui/es/Sender';
import type { ReactNode } from 'react';

/** Optional, platform-owned composer decoration. No platform data or protocol in Open Core. */
export interface ConversationComposerContext {
  botId: string;
  ownerId: string;
  /** Source Bot Owner for read-only knowledge queries; not native chat addressing. */
  knowledgeOwnerId?: string;
  sessionId: string;
  spaceId?: string;
  botType?: string;
  runtimeStage: 'draft' | 'verify' | 'online';
}
export interface ConversationComposerValue {
  command?: CommandConfig;
  header?: ReactNode;
  /** 平台侧覆盖输入框默认提示文案（capability 覆盖式注入；Open Core 不持有平台命令文案）。 */
  placeholder?: string;
  onSend: (content: string, context?: SubmitContext) => void;
}
export interface ConversationComposerProps extends ConversationComposerValue {
  context: ConversationComposerContext;
  children: (value: ConversationComposerValue) => ReactNode;
}
