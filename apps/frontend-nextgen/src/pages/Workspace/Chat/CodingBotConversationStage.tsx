import type { HumanIdentity } from '@/capabilities';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { CodingBotStageBoundary } from './CodingBotStageBoundary';
import { ConversationSessionPlaceholder } from './components/ConversationSessionPlaceholder';
import type { ConversationSessionPlaceholderModel } from './hooks/useConversationSessionPlaceholder';

/**
 * Coding Bot 主舞台。
 *
 * 首会话创建依赖 AIXCoding 子应用注册的 Controller，因此确认无会话时仍需保持
 * Embedded Stage 挂载，只在它上方覆盖 TeamClaw 的统一会话空态。创建成功后仅更新
 * sessionId，避免为首会话重新挂载 qiankun 子应用。
 */
export function CodingBotConversationStage({
  bot,
  session,
  human,
  placeholder,
  onOpenSessionList,
}: {
  bot: ChatBotView;
  session?: BotChatSessionView | null;
  human: HumanIdentity | null;
  placeholder?: ConversationSessionPlaceholderModel | null;
  onOpenSessionList(): void;
}): JSX.Element {
  const showPlaceholder = placeholder !== undefined && placeholder !== null;

  return (
    <div className="relative flex min-h-0 min-w-0 flex-1">
      {/* Placeholder 永远覆盖在 Stage 上方，而不是替换 Stage；这样“未选择 Session”
          仍能 bootstrap 目标 Bot Runtime 并注册 controller。 */}
      <div
        aria-hidden={showPlaceholder ? true : undefined}
        className={`flex min-h-0 min-w-0 flex-1 ${showPlaceholder ? 'invisible pointer-events-none' : ''}`}
      >
        <CodingBotStageBoundary bot={bot} session={session} human={human} />
      </div>

      {showPlaceholder && placeholder && (
        <div className="absolute inset-0 z-10 flex bg-background">
          <ConversationSessionPlaceholder model={placeholder} onOpenSessionList={onOpenSessionList} />
        </div>
      )}
    </div>
  );
}
