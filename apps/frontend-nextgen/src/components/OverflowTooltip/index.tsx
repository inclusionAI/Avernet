import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { cn } from '@/utils/cn';
import React from 'react';

export interface OverflowTooltipProps {
  children: React.ReactElement;
  /** Delayed-hover duration for the full text. */
  delayDuration?: number;
  contentClassName?: string;
}

/** Renders a tooltip only after the trigger's real DOM box reports text overflow. */
export const OverflowTooltip = React.memo(function OverflowTooltip({
  children,
  delayDuration = 350,
  contentClassName,
}: OverflowTooltipProps) {
  const triggerRef = React.useRef<HTMLButtonElement | null>(null);
  const [content, setContent] = React.useState({ text: '', overflowing: false });
  const [requestedOpen, setRequestedOpen] = React.useState(false);

  const measure = React.useCallback(() => {
    const element = triggerRef.current;
    const text = element?.textContent ?? '';
    const overflowing = Boolean(
      text && element && element.clientWidth > 0 && element.scrollWidth > element.clientWidth,
    );

    setContent((current) =>
      current.text === text && current.overflowing === overflowing ? current : { text, overflowing },
    );
    if (!overflowing) setRequestedOpen(false);
    return overflowing;
  }, []);

  React.useLayoutEffect(() => {
    measure();
    const element = triggerRef.current;
    const observer = element && typeof ResizeObserver === 'undefined' ? undefined : element ? new ResizeObserver(measure) : undefined;
    observer?.observe(element as HTMLElement);
    window.addEventListener('resize', measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [children, measure]);

  return (
    <TooltipProvider delayDuration={delayDuration}>
      <Tooltip
        open={content.overflowing && requestedOpen}
        onOpenChange={(open) => setRequestedOpen(open && measure())}
      >
        <TooltipTrigger asChild ref={triggerRef} onPointerEnter={measure} onFocus={measure}>
          {children}
        </TooltipTrigger>
        <TooltipContent
          className={cn(
            'max-h-[min(320px,calc(100vh-32px))] max-w-[min(400px,calc(100vw-32px))] overflow-auto whitespace-pre-wrap break-words [overflow-wrap:anywhere]',
            contentClassName,
          )}
          onClick={(event) => event.stopPropagation()}
        >
          {content.text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
});
