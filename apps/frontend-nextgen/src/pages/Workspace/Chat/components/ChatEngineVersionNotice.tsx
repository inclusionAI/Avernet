import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { IconButton } from '@/components/ui/IconButton';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { engineVersionTooltip, engineVersionUiState } from '@/domain/botEngineVersion';
import type { BotEngineVersionState } from '@/hooks/useBotEngineVersions';
import { X } from 'lucide-react';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';

export interface ChatEngineVersionNoticeProps {
  state: BotEngineVersionState;
  onOpen: () => void;
  onDismiss: () => void;
}

export function ChatEngineVersionNotice({ state, onOpen, onDismiss }: ChatEngineVersionNoticeProps) {
  const updatable = state.kind === 'ready' && engineVersionUiState(state.observation) === 'updatable';
  if (!updatable) return null;

  const label = '引擎有更新';
  const description = engineVersionTooltip(state.observation);
  return (
    <div className="flex items-center gap-1" data-testid="chat-engine-version-notice">
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="sm" className="h-auto rounded-full p-0" aria-label={label} onClick={() => { Tracert.click(Points.workspaceChat.engineUpdateEntry); onOpen(); }}>
              <Badge tone="warning" className="cursor-pointer">
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
