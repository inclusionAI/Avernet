import { Button } from '@/components/ui';
import { useFuseStore } from '@/stores/fuseStore';
import { cn } from '@/utils/cn';
import { Brain } from 'lucide-react';
import { useMemo, useState } from 'react';

interface FuseFloatButtonProps {
  onClick: () => void;
  sessionId?: string | null;
}

/** 融合模式悬浮按钮：右下角固定，hover 展开文字，当前会话有未读回答时显示红点。 */
export function FuseFloatButton({ onClick, sessionId }: FuseFloatButtonProps) {
  const unreadSessionIds = useFuseStore((s) => s.unreadSessionIds);
  const hasUnread = useMemo(() => (sessionId ? !!unreadSessionIds[sessionId] : false), [unreadSessionIds, sessionId]);
  const [hovered, setHovered] = useState(false);
  return (
    <div
      className="fixed bottom-[200px] right-6 z-50"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <Button
        variant="ghost"
        size="sm"
        onClick={onClick}
        className={cn(
          'flex h-10 items-center overflow-hidden rounded-full border border-border bg-background text-foreground shadow-md hover:shadow-lg !px-0 !justify-start',
          hovered ? 'w-[120px]' : 'w-10',
        )}
      >
        <span className="ml-1.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10">
          <Brain className="h-4 w-4 text-primary" />
        </span>
        <span
          className={cn(
            'whitespace-nowrap pl-2 pr-3 text-sm font-medium transition-opacity',
            hovered ? 'opacity-100' : 'opacity-0',
          )}
        >
          融合模式
        </span>
        {hasUnread && (
          <span className="absolute right-0.5 top-0.5 h-2.5 w-2.5 rounded-full bg-destructive ring-2 ring-background" />
        )}
      </Button>
    </div>
  );
}
