import { getCapabilities } from '@/capabilities';
import { useState } from 'react';
import type { ComponentProps } from 'react';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { CodingBotConversationStage } from './CodingBotConversationStage';

/** One stage per authenticated workspace, never one stage per Bot. */
export function RetainedCodingBotStage(props: Omit<ComponentProps<typeof CodingBotConversationStage>, 'bot'> & { bot: ChatBotView | null }) {
  const [retainedBot, setRetainedBot] = useState(props.bot);
  if (props.bot && props.bot !== retainedBot) setRetainedBot(props.bot);
  const bot = props.bot ?? retainedBot;
  const active = props.bot !== null;
  const retain = getCapabilities().getCodingBotConversationHost().value?.supportsWorkspaceSuspension === true;
  if (!bot || (!active && !retain)) return null;
  return (
    <div className="flex min-h-0 min-w-0 flex-1" style={active ? undefined : { display: 'none' }} aria-hidden={!active}>
      <CodingBotConversationStage {...props} bot={bot} workspaceActive={active}
        session={active ? props.session : null} placeholder={active ? props.placeholder : null} />
    </div>
  );
}
