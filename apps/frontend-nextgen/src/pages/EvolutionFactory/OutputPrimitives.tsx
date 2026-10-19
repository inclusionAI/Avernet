import { sanitizeMarkdownHtml } from '@/assets/TaskPanel/sanitizeHtml';
import { Button } from '@/components/ui/Button';
import type { JsonObject } from '@/domain/evolution/types';
import { history } from '@umijs/max';
import { ArrowUpRight } from 'lucide-react';
import MarkdownIt from 'markdown-it';
import { useMemo, type ReactNode } from 'react';
import { displayValue } from './presentation';
import { Hint } from './shared';

const markdown = new MarkdownIt({ html: false, breaks: true, linkify: true });
export function MarkdownReport({ content }: { content: string }) {
  const html = useMemo(() => sanitizeMarkdownHtml(markdown.render(content)), [content]);
  return (
    <div
      className="prose prose-sm max-w-none overflow-x-auto break-words dark:prose-invert"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
export const object = (v: unknown): JsonObject =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as JsonObject) : {};
export const array = (v: unknown): JsonObject[] => (Array.isArray(v) ? v.map(object) : []);
export const present = (v: JsonObject) => Object.keys(v).length > 0;
export const text = displayValue;
export const delta = (v: unknown) => (typeof v === 'number' && v > 0 ? `+${text(v)}` : text(v));
const tones = {
  blue: 'border-blue-200/70 bg-blue-50/60 dark:border-blue-800 dark:bg-blue-950/20',
  violet: 'border-violet-200/70 bg-violet-50/60 dark:border-violet-800 dark:bg-violet-950/20',
  green: 'border-emerald-200/70 bg-emerald-50/60 dark:border-emerald-800 dark:bg-emerald-950/20',
  amber: 'border-amber-200/70 bg-amber-50/60 dark:border-amber-800 dark:bg-amber-950/20',
};
export function ResultCard({
  title,
  tone = 'blue',
  extra,
  children,
}: {
  title: string;
  tone?: keyof typeof tones;
  extra?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={`min-w-0 rounded-xl border p-4 ${tones[tone]}`}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold">{title}</h4>
        {extra}
      </div>
      {children}
    </section>
  );
}
export function RunLink({ runId }: { runId: unknown }) {
  if (typeof runId !== 'string' || !runId) return null;
  return (
    <Hint content={runId}>
      <Button
        variant="ghost"
        size="sm"
        className="max-w-full px-0 text-primary"
        aria-label={`评估结果 · ${runId}`}
        onClick={() => history.push(`/evolution-factory/bench/runs/${encodeURIComponent(runId)}`)}
      >
        查看评测结果
        <ArrowUpRight size={14} className="ml-1 shrink-0" />
      </Button>
    </Hint>
  );
}
export function Values({ rows }: { rows: [string, unknown][] }) {
  return (
    <dl className="grid grid-cols-2 gap-3">
      {rows.map(([name, value]) => (
        <div key={name} className="min-w-0">
          <dt className="text-xs text-muted-foreground">{name}</dt>
          <dd className="mt-1 break-words font-mono text-sm font-semibold">{text(value)}</dd>
        </div>
      ))}
    </dl>
  );
}
