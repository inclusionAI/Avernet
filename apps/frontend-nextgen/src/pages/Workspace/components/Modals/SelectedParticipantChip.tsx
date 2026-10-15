import { Avatar, Button } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { SERVICE_BOT_STAGE_OPTIONS } from '@/domain/collaboration/serviceBotRouting';
import { X } from 'lucide-react';
import type { useServiceBotRouting } from '../../hooks/useServiceBotRouting';
import { ServiceBotRoutingControl } from './ServiceBotRoutingControl';

export function SelectedParticipantChip({
  bot,
  routing,
  onRemove,
}: {
  bot: { id: string; name: string; avatarUrl?: string };
  routing?: ReturnType<typeof useServiceBotRouting>;
  onRemove?: () => void;
}) {
  const state = routing?.states[bot.id];
  const stageLabel =
    state?.status === 'service' && state.routingMode === 'specified'
      ? SERVICE_BOT_STAGE_OPTIONS.find((option) => option.value === state.stage)?.label
      : undefined;
  return (
    <div
      role="group"
      aria-label={`${bot.name}已选成员`}
      className="inline-flex min-w-0 max-w-full flex-wrap items-center gap-1.5 rounded-xl border border-primary/20 bg-primary/10 py-1 pl-1.5 pr-1"
    >
      <span aria-hidden>
        <Avatar name={bot.name} src={bot.avatarUrl} size={16} />
      </span>
      <TooltipProvider delayDuration={300}>
        <Tooltip>
          <TooltipTrigger asChild>
            <span tabIndex={0} className="max-w-30 truncate text-xs font-semibold text-primary">
              {bot.name}
              {stageLabel ? `（${stageLabel}）` : ''}
            </span>
          </TooltipTrigger>
          <TooltipContent>{bot.name}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
      {routing && <ServiceBotRoutingControl actorId={bot.id} botName={bot.name} routing={routing} />}
      {onRemove && (
        <Button
          variant="ghost"
          size="sm"
          aria-label={`移除${bot.name}`}
          className="size-4 shrink-0 rounded-md border-0 p-0 text-primary hover:bg-primary/10"
          onClick={onRemove}
        >
          <X className="size-2.5" aria-hidden />
        </Button>
      )}
    </div>
  );
}
