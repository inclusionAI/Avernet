import { BotDisplayAvatar } from '@/components/Workspace/BotDisplayAvatar';
import { Button } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { SERVICE_BOT_STAGE_OPTIONS } from '@/domain/collaboration/serviceBotRouting';
import type { ServiceBotRoutingModel } from '@/extensions/contracts/groupRouting';
import { getExtensionSlots } from '@/extensions/slots';
import { X } from 'lucide-react';

// 选路控件槽位（collab 形态 null：chip 不渲染 segmented 选路控件）。
// 模块图不变量（见 src/extensions/slots.ts）：本文件解析的是「求值时刻」的注册表，不得被
// src/extensions 装配图静态引达（否则会捕获全 null 默认槽位，静默丢弃 Open 选路控件）。
// routing 的类型经槽位契约供给（collab-form Task 17 源切片类型完备）：useServiceBotRouting
// hooks 文件属 pages 裁剪面，保留链不得 type-only 依赖它（运行时守卫擦除此类边，源切片则会漏文件）。
const RoutingControl = getExtensionSlots().groupServiceBotRouting?.ServiceBotRoutingControl;

export function SelectedParticipantChip({
  bot,
  routing,
  onRemove,
}: {
  bot: { id: string; name: string; avatarUrl?: string };
  routing?: ServiceBotRoutingModel;
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
        <BotDisplayAvatar botId={bot.id} name={bot.name} avatarUrl={bot.avatarUrl} size={16} />
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
      {routing && RoutingControl ? <RoutingControl actorId={bot.id} botName={bot.name} routing={routing} /> : null}
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
