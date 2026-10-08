import { Button, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui';
import type { ParticipantView } from '@/domain/collaboration';
import type { ChatMessage } from '@tc-chat/core';
import { Square } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { getElapsedSeconds, resolveAbortableBotId } from './groupChatAbortHelpers';

interface ActiveBotRunSummary {
  botId: string;
  botName: string;
  runCount: number;
  startedAt?: number;
}

export function collectActiveBotRuns(messages: ChatMessage[], participants: ParticipantView[]): ActiveBotRunSummary[] {
  const participantNames = new Map(
    participants
      .filter((participant) => participant.kind === 'bot')
      .map((participant) => [participant.actorId, participant.name]),
  );
  const activeBots = new Map<string, ActiveBotRunSummary>();

  for (const message of messages) {
    const botId = resolveAbortableBotId(message, participants);
    if (!botId) continue;
    const current = activeBots.get(botId);
    if (!current) {
      activeBots.set(botId, {
        botId,
        botName: participantNames.get(botId) || 'Bot',
        runCount: 1,
        startedAt: message.createdAt,
      });
      continue;
    }
    current.runCount += 1;
    if (
      typeof message.createdAt === 'number' &&
      (typeof current.startedAt !== 'number' || message.createdAt < current.startedAt)
    ) {
      current.startedAt = message.createdAt;
    }
  }

  return [...activeBots.values()];
}

interface GroupChatActiveRunsProps {
  messages: ChatMessage[];
  participants: ParticipantView[];
  abortingBotIds: ReadonlySet<string>;
  onAbortBot: (botId: string) => Promise<void>;
}

/** “用户发言模式”行内的活动 Bot 终止项；顺序与活动消息首次出现顺序一致。 */
export function GroupChatActiveRuns({ messages, participants, abortingBotIds, onAbortBot }: GroupChatActiveRunsProps) {
  const activeBots = useMemo(() => collectActiveBotRuns(messages, participants), [messages, participants]);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (activeBots.length === 0) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [activeBots.length]);

  if (activeBots.length === 0) return null;

  return (
    <div className="ml-3 flex min-w-0 items-center gap-1 overflow-x-auto" aria-label="正在输出的 Bot">
      {activeBots.map((bot) => {
        const aborting = abortingBotIds.has(bot.botId);
        const elapsed = getElapsedSeconds(bot.startedAt, now);
        return (
          <div
            key={bot.botId}
            data-testid={`active-bot-run-${bot.botId}`}
            className="flex shrink-0 items-center gap-3 rounded-lg border border-border bg-background px-2.5 py-1.5 shadow-sm"
          >
            <div className="min-w-0">
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div className="max-w-28 truncate text-sm font-medium leading-tight text-foreground">
                      {bot.botName}
                    </div>
                  </TooltipTrigger>
                  <TooltipContent>{bot.botName}</TooltipContent>
                </Tooltip>
              </TooltipProvider>
              <div className="mt-0.5 flex items-center gap-1 whitespace-nowrap text-xs leading-tight text-muted-foreground tabular-nums">
                <span>{bot.runCount > 1 ? `${bot.runCount} 个输出中` : '输出中'}</span>
                {elapsed === null ? null : (
                  <>
                    <span aria-hidden="true">·</span>
                    <span>{elapsed}s</span>
                  </>
                )}
              </div>
            </div>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 shrink-0 rounded-md border border-transparent bg-transparent px-2 text-destructive shadow-none hover:border-destructive/20 hover:bg-destructive/10 hover:text-destructive focus-visible:border-destructive/30 focus-visible:bg-destructive/10"
              loading={aborting}
              disabled={aborting}
              aria-label={aborting ? `正在终止 ${bot.botName} 的输出` : `终止 ${bot.botName} 的输出`}
              leftIcon={<Square className="h-3 w-3 fill-current" />}
              onClick={() => {
                void onAbortBot(bot.botId);
              }}
            >
              {aborting ? '终止中…' : bot.runCount > 1 ? `终止(${bot.runCount})` : '终止'}
            </Button>
          </div>
        );
      })}
    </div>
  );
}
