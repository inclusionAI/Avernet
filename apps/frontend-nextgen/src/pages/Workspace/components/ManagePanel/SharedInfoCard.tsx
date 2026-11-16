import { Card, IconButton } from '@/components/ui';
import type { SharedInfoItem } from '@/services/workspace/groupContextService';
import { cn } from '@/utils/cn';
import { ChevronDown, ChevronUp, Copy } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

interface SharedInfoCardProps {
  item: SharedInfoItem;
  creator: string;
  sessionId?: string;
}

export function SharedInfoCard({ item, creator, sessionId }: SharedInfoCardProps) {
  const contentRef = useRef<HTMLParagraphElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [overflowing, setOverflowing] = useState(false);
  const time = new Date(item.gmt_modified);
  const displayTime = Number.isNaN(time.getTime()) ? item.gmt_modified : time.toLocaleString();

  useEffect(() => {
    const content = contentRef.current;
    if (!content) return;
    const measure = () => {
      const lineHeight = Number.parseFloat(getComputedStyle(content).lineHeight);
      setOverflowing(content.scrollHeight > lineHeight * 5 + 1);
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(content);
    return () => observer.disconnect();
  }, [item.content]);

  async function copyContent() {
    try {
      await navigator.clipboard.writeText(item.content);
      toast.success('已复制共享信息');
    } catch {
      toast.error('复制失败');
    }
  }

  return (
    <Card className="space-y-2 bg-muted/40 p-3">
      <h4 className="break-words text-sm font-semibold">{item.name}</h4>
      {item.scope_level === 'round' && sessionId && (
        <p className="break-all text-xs text-muted-foreground">轮次 {item.scope.slice(sessionId.length + 1)}</p>
      )}
      <p ref={contentRef} className={cn('whitespace-pre-wrap break-words text-sm leading-5', !expanded && 'line-clamp-5')}>
        {item.content}
      </p>
      <div className="flex flex-wrap items-center gap-x-1 text-xs text-muted-foreground">
        <span className="break-words">{creator} 更新于 {displayTime}</span>
        {overflowing && (
          <IconButton
            type="button"
            size="sm"
            label={expanded ? '收起正文' : '展开正文'}
            aria-expanded={expanded}
            icon={expanded ? <ChevronUp className="size-4" aria-hidden /> : <ChevronDown className="size-4" aria-hidden />}
            onClick={() => setExpanded(!expanded)}
          />
        )}
        <IconButton type="button" size="sm" label="复制正文" icon={<Copy className="size-4" aria-hidden />} onClick={copyContent} />
      </div>
    </Card>
  );
}
