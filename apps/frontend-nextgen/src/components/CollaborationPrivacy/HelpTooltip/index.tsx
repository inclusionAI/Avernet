import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { CircleAlert, Info } from 'lucide-react';
import type { ReactNode } from 'react';
import { collaborationPrivacyTooltipDelayMs } from '../interaction';

interface HelpTooltipProps {
  label: string;
  content: ReactNode;
}

export function LabelHelpTooltip({ label, content }: HelpTooltipProps) {
  return (
    <TooltipProvider delayDuration={collaborationPrivacyTooltipDelayMs}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant="ghost" size="icon" className="h-5 w-5 text-muted-foreground" aria-label={`${label}功能说明`}>
            <Info className="h-3.5 w-3.5" aria-hidden />
          </Button>
        </TooltipTrigger>
        <TooltipContent>{content}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

interface ControlStateTooltipProps extends HelpTooltipProps {
  status: string;
}

export function ControlStateTooltip({ label, status, content }: ControlStateTooltipProps) {
  return (
    <TooltipProvider delayDuration={collaborationPrivacyTooltipDelayMs}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant="ghost" size="icon" className="h-5 w-5 text-warning" aria-label={`${label}${status}原因`}>
            <CircleAlert className="h-3.5 w-3.5" aria-hidden />
          </Button>
        </TooltipTrigger>
        <TooltipContent>{content}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
