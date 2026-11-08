import type { JsonObject } from '@/domain/evolution/types';
import { eventContentBlocks, eventLabel, eventRole, eventUsageText, type BenchSessionEvent } from './benchSession';
import { JsonView } from './shared';
import { MarkdownReport } from './StepOutput';

export function BenchSessionReader({ session }: { session: JsonObject }) {
  const events = Array.isArray(session.events) ? session.events : [];
  return (
    <div className="min-w-0 space-y-3">
      {!events.length && (
        <p className="text-sm text-muted-foreground">该 Session 未提供可解析事件，可展开原始内容查看。</p>
      )}
      {events.map((raw, index) => {
        const event = (raw && typeof raw === 'object' ? raw : { type: 'unknown', raw }) as BenchSessionEvent;
        return (
          <article key={index} className="min-w-0 space-y-3 rounded-lg border border-border p-4">
            <header className="flex flex-wrap items-center gap-2 text-xs">
              <strong>
                {index + 1}. {eventLabel(event)}
              </strong>
              <span className="text-muted-foreground">{eventRole(event)}</span>
              <time className="text-muted-foreground">{event.timestamp}</time>
            </header>
            {eventUsageText(event) && <p className="text-xs text-muted-foreground">Token · {eventUsageText(event)}</p>}
            {eventContentBlocks(event).map((block, i) =>
              block.tone === 'thinking' ? (
                <details key={i} className="rounded-lg bg-muted/50 p-3">
                  <summary className="cursor-pointer text-xs text-muted-foreground">思考</summary>
                  <pre className="mt-2 max-h-[32rem] overflow-auto whitespace-pre-wrap break-words text-xs">
                    {block.text}
                  </pre>
                </details>
              ) : (
                <div key={i} className="space-y-2">
                  <p className="text-xs font-medium text-muted-foreground">{block.label}</p>
                  {block.tone === 'tool' ? (
                    <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted p-3 text-xs">
                      {block.text}
                    </pre>
                  ) : (
                    <MarkdownReport content={block.text} />
                  )}
                </div>
              ),
            )}
          </article>
        );
      })}
      <details className="rounded-lg border border-border p-3">
        <summary className="cursor-pointer text-xs text-muted-foreground">原始 Session</summary>
        <JsonView value={session} />
      </details>
    </div>
  );
}
export function BenchBreakdown({ value }: { value: unknown }) {
  if (!value || typeof value !== 'object') return null;
  const items = Array.isArray(value)
    ? value
    : Object.entries(value).map(([name, item]) =>
        typeof item === 'object' && item ? { name, ...item } : { name, value: item },
      );
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {items.map((item, index) => {
        const row: JsonObject = item && typeof item === 'object' ? (item as JsonObject) : { value: item };
        return (
          <div key={index} className="min-w-0 space-y-2 rounded-lg border border-border p-3 text-sm">
            <strong className="break-words">
              {String(row.name ?? row.label ?? row.criterion ?? `评分项 ${index + 1}`)}
            </strong>
            <p>
              得分：{String(row.score ?? row.value ?? '—')}
              {row.maxScore !== null && row.maxScore !== undefined ? ` / ${row.maxScore}` : ''}
            </p>
            {Boolean(row.reason || row.feedback || row.description) && (
              <MarkdownReport content={String(row.reason || row.feedback || row.description)} />
            )}
          </div>
        );
      })}
    </div>
  );
}
