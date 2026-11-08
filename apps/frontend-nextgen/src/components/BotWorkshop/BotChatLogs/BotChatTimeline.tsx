import { Button } from '@/components/ui/Button';
import type { BotChatDetail, BotChatObservation } from '@/domain/botChats';
import { cn } from '@/utils/cn';
import { ChevronDown, ChevronRight, Circle, Terminal, Zap } from 'lucide-react';
import { useState } from 'react';

export type BotChatSelection = { kind: 'trace' } | { kind: 'observation'; item: BotChatObservation };

const formatLatency = (value: number) => (value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${Math.round(value)}ms`);

const iconFor = (type: string) => {
  const upper = type.toUpperCase();
  if (upper === 'GENERATION' || upper === 'LLM')
    return <span className="size-2 rounded-full bg-primary" />;
  return <Terminal className="size-3 text-primary" />;
};

function ObservationNode({
  item,
  level,
  selectedId,
  onSelect,
}: {
  item: BotChatObservation;
  level: number;
  selectedId?: string;
  onSelect: (item: BotChatObservation) => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const hasChildren = item.children.length > 0;
  return (
    <div>
      <div className="relative">
        {level > 0 ? <span className="absolute left-[-16px] top-1/2 h-px w-4 bg-border" /> : null}
        <Button
          variant="ghost"
          size="sm"
          className={cn(
            'h-auto w-full justify-start gap-2 rounded-lg border px-3 py-2 text-left text-xs font-normal',
            selectedId === item.id
              ? 'border-primary/50 bg-primary/10 text-primary'
              : 'border-transparent hover:bg-muted',
          )}
          style={{ marginLeft: level * 16, width: `calc(100% - ${level * 16}px)` }}
          onClick={(event) => {
            if (hasChildren && (event.target as HTMLElement | null)?.closest('[data-observation-toggle]')) {
              event.stopPropagation();
              setExpanded((value) => !value);
              return;
            }
            onSelect(item);
          }}
        >
          {hasChildren ? (
            <span data-observation-toggle className="flex size-4 shrink-0 items-center justify-center">
              {expanded ? (
                <ChevronDown className="size-3 text-muted-foreground" />
              ) : (
                <ChevronRight className="size-3 text-muted-foreground" />
              )}
            </span>
          ) : (
            <span className="size-4 shrink-0" />
          )}
          <span className="flex size-4 shrink-0 items-center justify-center rounded bg-secondary">
            {iconFor(item.type)}
          </span>
          <span className="min-w-0 flex-1 truncate font-medium">{item.name || item.type}</span>
          <span className="shrink-0 text-[11px] text-success">✓</span>
          <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
            {formatLatency(item.latencyMs)}
          </span>
        </Button>
      </div>
      {hasChildren && expanded
        ? item.children.map((child) => (
            <ObservationNode
              key={child.id}
              item={child}
              level={level + 1}
              selectedId={selectedId}
              onSelect={onSelect}
            />
          ))
        : null}
    </div>
  );
}

interface Props {
  detail: BotChatDetail;
  selection: BotChatSelection;
  onSelect: (selection: BotChatSelection) => void;
}

export function BotChatTimeline({ detail, selection, onSelect }: Props) {
  return (
    <section className="flex min-h-[560px] min-w-0 flex-col border-b border-border bg-muted lg:border-b-0 lg:border-r">
      <div className="border-b border-border px-4 py-3">
        <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">Timeline</div>
        <div className="mt-1 truncate text-xs text-muted-foreground">当前：{detail.name || detail.id}</div>
      </div>
      <div className="app-scrollbar min-h-0 flex-1 overflow-auto p-3">
        <div className="relative space-y-0 before:absolute before:bottom-3 before:left-[21px] before:top-4 before:w-px before:bg-border">
          <Button
            variant="ghost"
            size="sm"
            className={cn(
              'relative z-10 h-auto w-full justify-start gap-3 rounded-lg border px-3 py-2 text-left',
              selection.kind === 'trace'
                ? 'border-primary/50 bg-card shadow-sm'
                : 'border-transparent hover:bg-card/70',
            )}
            onClick={() => onSelect({ kind: 'trace' })}
          >
            <span className="flex size-4 shrink-0 items-center justify-center rounded bg-primary/10">
              <Zap className="size-3 fill-primary text-primary" />
            </span>
            <span className="min-w-0 flex-1 truncate text-xs font-semibold text-foreground">
              {detail.name || detail.id}
            </span>
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
              {formatLatency(detail.latencyMs)}
            </span>
          </Button>
          {detail.observations.map((item) => (
            <ObservationNode
              key={item.id}
              item={item}
              level={1}
              selectedId={selection.kind === 'observation' ? selection.item.id : undefined}
              onSelect={(observation) => onSelect({ kind: 'observation', item: observation })}
            />
          ))}
          {!detail.observations.length ? (
            <div className="relative z-10 flex items-center gap-2 px-3 py-5 text-xs text-muted-foreground">
              <Circle className="size-3" />
              暂无 Observation 数据
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}
