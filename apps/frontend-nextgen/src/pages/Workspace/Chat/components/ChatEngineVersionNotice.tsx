import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { IconButton } from '@/components/ui/IconButton';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { engineVersionTooltip, engineVersionUiState } from '@/domain/botEngineVersion';
import type { BotEngineVersionState } from '@/hooks/useBotEngineVersions';
import { X } from 'lucide-react';

export interface ChatEngineVersionNoticeProps {
  state: BotEngineVersionState;
  onOpen: () => void;
  onDismiss: () => void;
}

export function ChatEngineVersionNotice({ state, onOpen, onDismiss }: ChatEngineVersionNoticeProps) {
  const updatable = state.kind === 'ready' && engineVersionUiState(state.observation) === 'updatable';
  const needsInstance = state.kind === 'select_instance';
  if (!updatable && !needsInstance) return null;

  const label = updatable ? '引擎有更新' : '选择引擎实例';
  const description = updatable ? engineVersionTooltip(state.observation) : '此 Bot 有多个运行实例，请选择要查看的实例';
  return (
    <div className="flex items-center gap-1" data-testid="chat-engine-version-notice">
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="sm" className="h-auto rounded-full p-0" aria-label={label} onClick={onOpen}>
              <Badge tone={updatable ? 'warning' : 'neutral'} className="cursor-pointer">
                {label}
              </Badge>
            </Button>
          </TooltipTrigger>
          <TooltipContent>{description}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <IconButton
        label="关闭提示"
        ariaLabel="关闭当前会话的引擎更新提示"
        icon={<X className="size-3.5" />}
        size="sm"
        onClick={onDismiss}
      />
    </div>
  );
}
