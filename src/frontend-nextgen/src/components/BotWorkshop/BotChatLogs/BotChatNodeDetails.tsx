import { Badge } from '@/components/ui/Badge';
import { Segmented } from '@/components/ui/Segmented';
import type { BotChatDetail, BotChatObservation } from '@/domain/botChats';
import { Bot, Terminal, Zap } from 'lucide-react';
import { useState } from 'react';
import type { BotChatSelection } from './BotChatTimeline';

const renderValue = (value: unknown) => {
  if (value === undefined || value === null || value === '') return '-';
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
};

function Value({ title, value }: { title: string; value: unknown }) {
  return (
    <section>
      <h4 className="mb-2 text-sm font-semibold text-[var(--color-fg)]">{title}</h4>
      <pre className="app-scrollbar max-h-[300px] overflow-auto whitespace-pre-wrap break-all rounded-xl border border-[var(--color-border)] bg-white p-4 text-xs leading-6 text-[var(--color-fg)]">
        {renderValue(value)}
      </pre>
    </section>
  );
}

function iconFor(selection: BotChatSelection) {
  if (selection.kind === 'trace')
    return <Zap className="size-4 fill-[var(--color-primary)] text-[var(--color-primary)]" />;
  return selection.item.type.toUpperCase() === 'GENERATION' || selection.item.type.toUpperCase() === 'LLM' ? (
    <Bot className="size-4 text-[var(--color-primary)]" />
  ) : (
    <Terminal className="size-4 text-[var(--color-primary)]" />
  );
}

export function BotChatNodeDetails({ detail, selection }: { detail: BotChatDetail; selection: BotChatSelection }) {
  const [tab, setTab] = useState<'run' | 'metadata'>('run');
  const isTrace = selection.kind === 'trace';
  const data: BotChatDetail | BotChatObservation = isTrace ? detail : selection.item;
  const title = isTrace ? detail.name || detail.id : selection.item.name || selection.item.type;
  const type = isTrace ? 'TRACE' : selection.item.type;

  return (
    <section className="flex min-h-[560px] min-w-0 flex-col bg-white">
      <header className="border-b border-[var(--color-border)] px-5 py-4">
        <div className="flex items-center gap-2">
          <span className="flex size-6 items-center justify-center rounded bg-[var(--color-primary-soft)]">
            {iconFor(selection)}
          </span>
          <h3 className="m-0 min-w-0 flex-1 truncate text-base font-semibold text-[var(--color-fg)]">{title}</h3>
          <Badge tone={isTrace ? 'primary' : type === 'TOOL' ? 'warning' : 'neutral'}>{type}</Badge>
        </div>
        <p className="mt-2 font-mono text-[11px] text-[var(--color-muted)]">
          {isTrace ? detail.id : selection.item.id}
        </p>
        <dl className="mt-4 grid gap-3 text-xs text-[var(--color-muted)] sm:grid-cols-2 xl:grid-cols-4">
          <div>
            <dt>模型</dt>
            <dd className="text-[var(--color-fg)]">
              {isTrace ? detail.model || '-' : selection.item.modelName || '-'}
            </dd>
          </div>
          <div>
            <dt>耗时</dt>
            <dd className="text-[var(--color-fg)]">{data.latencyMs.toFixed(0)} ms</dd>
          </div>
          <div>
            <dt>Token</dt>
            <dd className="text-[var(--color-fg)]">{data.totalTokens}</dd>
          </div>
          <div>
            <dt>成本</dt>
            <dd className="text-[var(--color-fg)]">${data.totalCost.toFixed(6)}</dd>
          </div>
        </dl>
      </header>
      <div className="border-b border-[var(--color-border)] px-5 py-3">
        <Segmented
          value={tab}
          options={[
            { value: 'run', label: 'Run' },
            { value: 'metadata', label: 'Metadata' },
          ]}
          onChange={setTab}
          className="w-fit"
        />
      </div>
      <div className="app-scrollbar min-h-0 flex-1 space-y-5 overflow-auto p-5">
        {tab === 'run' ? (
          <>
            <Value title="Input" value={data.input} />
            <Value title="Output" value={data.output} />
          </>
        ) : (
          <Value title="Metadata" value={data.metadata} />
        )}
      </div>
    </section>
  );
}
