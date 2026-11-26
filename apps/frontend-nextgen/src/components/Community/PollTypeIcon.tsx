import { ChartLine } from 'lucide-react';
import { cn } from '@/utils/cn';

interface PollTypeIconProps {
  className?: string;
}

export function PollTypeIcon({ className }: PollTypeIconProps) {
  return (
    <ChartLine
      role="img"
      aria-label="投票主题"
      className={cn('shrink-0 text-primary', className)}
    />
  );
}
