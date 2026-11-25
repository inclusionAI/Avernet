import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
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

// 可移除 chip:label 与 × 同居一颗胶囊(交互分区:标题区打开更新、×区退出提示),
// 两个分区共用 pill 底色与 rounded-full hover 加深(warning 同色系),避免 × 读作独立功能按钮。
export function ChatEngineVersionNotice({ state, onOpen, onDismiss }: ChatEngineVersionNoticeProps) {
  const updatable = state.kind === 'ready' && engineVersionUiState(state.observation) === 'updatable';
  if (!updatable) return null;

  const label = '引擎有更新';
  const description = engineVersionTooltip(state.observation);
  return (
    <TooltipProvider>
      <Tooltip>
        {/* pr-1 让 × 贴胶囊右缘泊位,gap-0.5 与标题留 2px 呼吸 */}
        <Badge tone="warning" className="gap-0.5 pl-2 pr-1" data-testid="chat-engine-version-notice">
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="h-auto rounded-full p-0 text-[11px] hover:bg-warning/20 hover:text-warning"
              aria-label={label}
              onClick={() => { Tracert.click(Points.workspaceChat.engineUpdateEntry); onOpen(); }}
            >
              {label}
            </Button>
          </TooltipTrigger>
          <Button
            variant="ghost"
            size="icon"
            className="size-4 rounded-full p-0 hover:bg-warning/20 hover:text-warning"
            aria-label="关闭当前会话的引擎更新提示"
            onClick={onDismiss}
          >
            <X aria-hidden className="size-2.5" />
          </Button>
        </Badge>
        <TooltipContent>{description}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
