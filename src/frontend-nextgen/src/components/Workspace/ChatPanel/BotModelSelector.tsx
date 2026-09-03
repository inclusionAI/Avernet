import { Badge, Button, Spin } from '@/components/ui';
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
}

/** bot 单聊头部模型切换按钮，参考 open-claw 我的 Bot 会话模型选择器。 */
export function BotModelSelector({ models, activeModelId, loading, disabled, onSelect }: BotModelSelectorProps) {
  const [open, setOpen] = useState(false);
  const activeModel = useMemo(
    () => models.find((model) => model.modelId === activeModelId) ?? null,
    [models, activeModelId],
  );
  const label = activeModel?.name || activeModelId || '选择模型';

  return (
    <Popover open={open} onOpenChange={setOpen}>
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
          <span className="max-w-[120px] truncate">{label}</span>
          <ChevronDown className={cn('h-3 w-3 transition-transform', open && 'rotate-180')} />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-64 p-1">
        {loading ? (
          <Spin tip="加载模型列表..." />
        ) : models.length === 0 ? (
          <p className="px-3 py-6 text-center text-xs text-muted-foreground">暂无可用模型</p>
        ) : (
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
                    <span className="min-w-0 truncate">{model.name}</span>
                  </span>
                  <span className="flex items-center gap-1.5">
                    {model.provider ? <Badge tone="neutral">{model.provider}</Badge> : null}
                    {selected ? <Check className="h-4 w-4 text-primary" /> : null}
                  </span>
                </Button>
              );
            })}
          </div>
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
  const botModels = useBotModels(bot, session, activeIdentityId, onSessionModelChange);

  return (
    <BotModelSelector
      models={botModels.models}
      activeModelId={botModels.activeModelId}
      loading={botModels.isLoadingModels}
      onSelect={(modelId) => void botModels.selectModel(modelId)}
    />
  );
}
