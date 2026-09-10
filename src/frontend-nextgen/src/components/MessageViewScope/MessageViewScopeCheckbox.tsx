import { Checkbox } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { PARTICIPANT_ONLY_CHECKBOX } from '@/domain/collaboration/messageViewScope';
import { HelpCircle } from 'lucide-react';

export interface MessageViewScopeCheckboxProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
}

/** 「加入当前会话」弹窗的参与者视角 checkbox 卡片（视觉稿：加入当前会话按钮效果.png）。 */
export function MessageViewScopeCheckbox({ checked, onCheckedChange, disabled }: MessageViewScopeCheckboxProps) {
  return (
    <div className="rounded-lg bg-muted/60 px-3 py-2.5 text-left">
      <label className="flex items-center gap-2">
        <Checkbox checked={checked} disabled={disabled} onCheckedChange={onCheckedChange} />
        <span className="text-sm font-medium text-foreground">{PARTICIPANT_ONLY_CHECKBOX.label}</span>
        <TooltipProvider delayDuration={0}>
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                tabIndex={0}
                aria-label={PARTICIPANT_ONLY_CHECKBOX.tooltip}
                className="flex items-center outline-none"
              >
                <HelpCircle className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
              </span>
            </TooltipTrigger>
            <TooltipContent>{PARTICIPANT_ONLY_CHECKBOX.tooltip}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </label>
      <p className="mt-1 pl-6 text-xs text-muted-foreground">{PARTICIPANT_ONLY_CHECKBOX.hint}</p>
    </div>
  );
}
