import { Badge, Button, Spin, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import { useBotModels } from '@/pages/Workspace/hooks/useBotModels';
import type { BotChatSessionView, BotModelView, ChatBotView } from '@/services/workspace/botSessionService';
import { cn } from '@/utils/cn';
import { Bot, Check, ChevronDown, Cpu } from 'lucide-react';
import { useMemo, useState } from 'react';

interface BotModelSelectorProps {
  models: BotModelView[];
  activeModelId: string | null;
  loading?: boolean;
  disabled?: boolean;
  onSelect: (modelId: string) => void;
  onOpen?: () => void;
}

/** bot 单聊头部模型切换按钮，参考 open-claw 我的 Bot 会话模型选择器。 */
export function BotModelSelector({ models, activeModelId, loading, disabled, onSelect, onOpen }: BotModelSelectorProps) {
  const [open, setOpen] = useState(false);
  const activeModel = useMemo(
    () => models.find((model) => model.modelId === activeModelId) ?? null,
    [models, activeModelId],
  );
  const label = activeModel?.name || activeModelId || '选择模型';

  return (
    <Popover open={open} onOpenChange={(next) => { setOpen(next); if (next) onOpen?.(); }}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          disabled={disabled || loading}
          className={cn(
            'h-8 gap-1.5 rounded-lg border border-border bg-background px-3 text-xs font-medium text-foreground',
            open && 'border-primary text-primary',
          )}
        >
          <Cpu className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="whitespace-nowrap">{label}</span>
          <ChevronDown className={cn('h-3 w-3 transition-transform', open && 'rotate-180')} />
        </Button>
      </PopoverTrigger>
      {/* 宽度随最长模型名自适应（min-w 保底、max-w 兜底防爆屏），名称超宽截断时由 tooltip 补全。 */}
      <PopoverContent align="end" className="w-auto min-w-64 max-w-[360px] p-1">
        {loading ? (
          <Spin tip="加载模型列表..." />
        ) : models.length === 0 ? (
          <p className="px-3 py-6 text-center text-xs text-muted-foreground">暂无可用模型</p>
        ) : (
          <TooltipProvider delayDuration={200}>
            <div className="max-h-72 overflow-y-auto">
              {models.map((model) => {
                const selected = model.modelId === activeModelId;
                return (
                  <Button
                    key={model.modelId}
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setOpen(false);
                      onSelect(model.modelId);
                    }}
                    className={cn(
                      'h-auto w-full justify-between rounded-md px-3 py-2 text-left text-sm font-normal',
                      selected ? 'bg-primary/10 text-primary' : 'text-foreground hover:bg-muted',
                    )}
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <Bot className="h-4 w-4 flex-none text-muted-foreground" />
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="min-w-0 truncate">{model.name}</span>
                        </TooltipTrigger>
                        <TooltipContent>{model.name}</TooltipContent>
                      </Tooltip>
                      {model.badge ? (
                        <Badge tone="primary" className="flex-none">
                          {model.badge.label}
                        </Badge>
                      ) : null}
                    </span>
                    <span className="flex items-center gap-1.5">
                      {model.badge?.link ? (
                        <span
                          role="link"
                          tabIndex={0}
                          className="flex-none cursor-pointer text-xs font-medium text-primary hover:underline"
                          onClick={(event) => {
                            // 「查看介绍」只跳转：不触发选模型、不收起下拉。
                            event.stopPropagation();
                            event.preventDefault();
                            if (model.badge?.link) window.open(model.badge.link, '_blank', 'noopener,noreferrer');
                          }}
                          onKeyDown={(event) => {
                            if (event.key !== 'Enter') return;
                            event.stopPropagation();
                            event.preventDefault();
                            if (model.badge?.link) window.open(model.badge.link, '_blank', 'noopener,noreferrer');
                          }}
                        >
                          查看介绍
                        </span>
                      ) : null}
                      {model.provider ? <Badge tone="neutral">{model.provider}</Badge> : null}
                      {selected ? <Check className="h-4 w-4 text-primary" /> : null}
                    </span>
                  </Button>
                );
              })}
            </div>
          </TooltipProvider>
        )}
      </PopoverContent>
    </Popover>
  );
}

interface BotModelSelectorContainerProps {
  chatBots: ChatBotView[];
  session: BotChatSessionView | null;
  activeIdentityId: string | null;
  onSessionModelChange: (botId: string, sessionId: string, model: string) => void;
}

/** 从当前 bot/session 拉取模型列表，并渲染模型切换按钮的容器组件。 */
export function BotModelSelectorContainer({
  chatBots,
  session,
  activeIdentityId,
  onSessionModelChange,
}: BotModelSelectorContainerProps) {
  const bot = chatBots.find((item) => item.botId === session?.botId) ?? null;
  const botModels = useBotModels(bot, session, activeIdentityId, onSessionModelChange, false);

  return (
    <BotModelSelector
      models={botModels.models}
      activeModelId={botModels.activeModelId}
      loading={botModels.isLoadingModels}
      disabled={bot?.isFriendBot}
      onOpen={botModels.loadModels}
      onSelect={(modelId) => void botModels.selectModel(modelId)}
    />
  );
}
