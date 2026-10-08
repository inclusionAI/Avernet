import { Button } from '@/components/ui';
import { useOpenSourceExperienceNotice } from '@/hooks/useOpenSourceExperienceNotice';
import { Info } from 'lucide-react';

export function OpenSourceExperienceNotice() {
  const { notice, visible, acknowledge } = useOpenSourceExperienceNotice();
  if (!notice || !visible) return null;

  return (
    <section
      role="status"
      aria-label="开源体验环境提示"
      className="flex min-h-[42px] w-full items-center justify-center gap-3 border-b border-primary/15 bg-background px-4 py-1.5 sm:px-6"
      style={{ backgroundColor: 'color-mix(in srgb, hsl(var(--primary)) 5%, hsl(var(--background)))' }}
    >
      <Info aria-hidden className="h-[18px] w-[18px] shrink-0 text-primary" />
      <span className="min-w-0 whitespace-normal text-xs leading-5 text-muted-foreground sm:text-sm">
        {notice.message}
      </span>
      <Button
        variant="ghost"
        size="sm"
        className="shrink-0 text-primary hover:bg-primary/10 hover:text-primary"
        onClick={acknowledge}
      >
        {notice.acknowledgeLabel}
      </Button>
    </section>
  );
}
