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
  /** 好友 Bot（他人分享、非 owner/协作者身份）会话标记：平台注入层据此收敛能力
   *  提示（如 /skill、/knowledge 等仅 owner/协作者可用的命令不在好友会话提示）。 */
  isFriendBot?: boolean;
  runtimeStage: 'draft' | 'verify' | 'online';
}
export interface ConversationComposerValue {
  command?: CommandConfig;
  header?: ReactNode;
  /** 平台侧覆盖输入框默认提示文案（capability 覆盖式注入；Open Core 不持有平台命令文案）。 */
  placeholder?: string;
  /** 平台侧注入「＋」菜单的额外菜单行（渲染函数收 close：点击后可收起外层菜单；
   * capability 自渲染；Open Core 不持有平台能力入口）。 */
  extraMenuRows?: (close: () => void) => ReactNode;
  /** 触发输入框命令（往草稿插入命令 token 并聚焦，唤起命令面板）；由舞台层提供实现。 */
  onInvokeCommand?: (command: string) => void;
  onSend: (content: string, context?: SubmitContext) => void;
}
export interface ConversationComposerProps extends ConversationComposerValue {
  context: ConversationComposerContext;
  children: (value: ConversationComposerValue) => ReactNode;
}
