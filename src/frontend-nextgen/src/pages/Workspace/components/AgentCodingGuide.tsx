import { Button, Empty } from '@/components/ui';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { ArrowRight, Sparkles } from 'lucide-react';

interface AgentCodingGuideProps {
  bot: ChatBotView;
  onOpen: (bot: ChatBotView) => void;
}

/** 选中 AgentCoding Bot 后，引导用户前往更适合的使用页面。 */
export function AgentCodingGuide({ bot, onOpen }: AgentCodingGuideProps) {
  const title = bot.templateName || 'AgentCoding Bot';

  return (
    <section className="flex min-w-0 flex-1 items-center justify-center bg-background px-6 py-10">
      <Empty
        className="w-full max-w-xl [&>p:first-of-type]:text-lg [&>p:nth-of-type(2)]:max-w-none [&>p:nth-of-type(2)]:whitespace-nowrap"
        icon={<Sparkles className="h-5 w-5" aria-hidden="true" />}
        title={title}
        description="为提供更好的使用体验，该 Bot 会在其他页面中为你提供服务。"
        action={
          <Button className="!text-sm cursor-pointer font-medium" size="sm" onClick={() => onOpen(bot)}>
            前往使用
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </Button>
        }
      />
    </section>
  );
}
