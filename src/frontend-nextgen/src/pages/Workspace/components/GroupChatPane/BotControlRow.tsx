import { Button } from '@/components/ui';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import { cn } from '@/utils/cn';
import { Bot, Check, ChevronDown, Loader2, MicOff, Volume2 } from 'lucide-react';
import { useState } from 'react';

/** 「Bot 控制」左侧状态展示配置（对齐 open-claw BotControlTab）。 */
export const BOT_MODE_DISPLAY = {
  auto: {
    title: 'Bot 自主发言中',
    description: 'Bot 将根据群内语境自主决策是否回复',
    icon: Volume2,
    label: '自动模式',
  },
  muted: {
    title: 'Bot 已禁言',
    description: 'Bot 将不会主动回复任何消息',
    icon: MicOff,
    label: '禁言模式',
  },
} as const;

export type BotMode = keyof typeof BOT_MODE_DISPLAY;

export interface BotControlRowProps {
  botMode: BotMode;
  switching: boolean;
  onModeChange: (mode: BotMode) => void;
}

/** 「Bot 控制」内容：状态展示 + 右侧发言模式切换 Popover。 */
export function BotControlRow({ botMode, switching, onModeChange }: BotControlRowProps) {
  const [open, setOpen] = useState(false);
  const display = BOT_MODE_DISPLAY[botMode];
  const DisplayIcon = display.icon ?? Bot;

  return (
    <div className="flex items-center justify-between py-1.5">
      <div className="flex items-center gap-2.5">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10">
          <DisplayIcon className="h-4 w-4 text-primary" />
        </div>
        <div className="min-w-0">
          <p className="text-sm font-medium leading-tight text-foreground">{display.title}</p>
          <p className="mt-0.5 text-xs leading-tight text-muted-foreground">{display.description}</p>
        </div>
      </div>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="secondary"
            size="sm"
            aria-label="切换 Bot 发言模式"
            disabled={switching}
            className="h-auto shrink-0 gap-3 rounded-xl px-3 py-2"
          >
            {switching ? (
              <Loader2 className="h-4 w-4 animate-spin text-primary" />
            ) : (
              <Bot className="h-4 w-4 text-primary" />
            )}
            <span className="text-left">
              <span className="block text-[10px] leading-none text-muted-foreground">Bot发言模式</span>
              <span className="mt-0.5 flex items-center gap-1 text-xs font-medium text-foreground">
                {display.label}
                <ChevronDown className="h-3 w-3 text-muted-foreground" />
              </span>
            </span>
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-64 p-1">
          {(Object.keys(BOT_MODE_DISPLAY) as BotMode[]).map((mode) => {
            const item = BOT_MODE_DISPLAY[mode];
            const ItemIcon = item.icon ?? Bot;
            const active = botMode === mode;
            return (
              <Button
                key={mode}
                variant="ghost"
                disabled={switching}
                onClick={() => {
                  onModeChange(mode);
                  setOpen(false);
                }}
                className={cn('h-auto w-full justify-start gap-2.5 px-2.5 py-2 text-left', active && 'bg-primary/10')}
              >
                <ItemIcon className={cn('h-4 w-4 shrink-0', active ? 'text-primary' : 'text-muted-foreground')} />
                <span className="min-w-0 flex-1">
                  <span className={cn('block text-xs font-medium', 'text-foreground')}>{item.label}</span>
                  <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground">
                    {item.description}
                  </span>
                </span>
                {active && <Check className="h-3.5 w-3.5 shrink-0 text-primary" />}
              </Button>
            );
          })}
        </PopoverContent>
      </Popover>
    </div>
  );
}

export default BotControlRow;
