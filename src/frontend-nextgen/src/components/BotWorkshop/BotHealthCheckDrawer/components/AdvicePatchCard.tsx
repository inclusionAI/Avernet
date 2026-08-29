import { Button } from '@/components/ui/Button';
import { Card, CardContent } from '@/components/ui/Card';
import type { BotHealthPatch } from '@/domain/botHealthCheck';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { useState } from 'react';

interface AdvicePatchCardProps {
  patch: BotHealthPatch;
  ordinal: number;
}

export function AdvicePatchCard({ patch, ordinal }: AdvicePatchCardProps) {
  const [expanded, setExpanded] = useState(false);
  const content = patch.advise?.advise_content ?? '';

  return (
    <Card className="rounded-xl border-[var(--color-border)]">
      <CardContent className="space-y-3 p-4">
        <div className="text-sm font-semibold text-[var(--color-fg)]">
          {ordinal}. {patch.name}
        </div>
        {patch.description ? <div className="text-sm text-[var(--color-muted)]">{patch.description}</div> : null}
        {!content ? <div className="text-sm italic text-[var(--color-muted)]">暂无建议内容</div> : null}
        {expanded && content ? (
          <div className="rounded-lg bg-[var(--color-panel-muted)] px-3 py-3 text-sm text-[var(--color-fg)]">
            {content}
          </div>
        ) : null}
        <Button
          variant="ghost"
          size="sm"
          className="px-0 text-[var(--color-warning)] hover:text-[var(--color-warning)]"
          onClick={() => setExpanded((v) => !v)}
          leftIcon={expanded ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
        >
          {expanded ? '收起建议' : '查看完整建议'}
        </Button>
      </CardContent>
    </Card>
  );
}
