import { getCapabilities } from '@/capabilities';
import type { ConversationComposerProps } from '@/capabilities/conversationComposer';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { resolveChatRuntimeStage } from '@/services/workspace/botSessionService';
import type { ReactNode } from 'react';

type Props = Omit<ConversationComposerProps, 'context'> & {
  bot: ChatBotView | null;
  session: BotChatSessionView | null;
};
export function ConversationComposerHost({ bot, session, children, ...value }: Props): ReactNode {
  const result = getCapabilities().getConversationComposer?.();
  const Extension = result?.status === 'available' ? result.value : null;
  // Never infer a target owner from the viewer, or decorate desktop/no-session input.
  const ownerId = bot?.ownerId;
  if (!Extension || !bot?.realBotId || !ownerId || !session || bot.botType === 'desktop') return children(value);
  const context = {
    botId: bot.realBotId,
    ownerId,
    sessionId: session.sessionId,
    spaceId: bot.spaceId,
    botType: bot.botType,
    runtimeStage: resolveChatRuntimeStage(bot),
  };
  return (
    <Extension key={JSON.stringify(context)} {...value} context={context}>
      {children}
    </Extension>
  );
}
