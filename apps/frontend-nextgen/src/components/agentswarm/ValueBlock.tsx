import { useState } from 'react';
import { JsonBlock } from './JsonBlock';

/**
 * Renders one agent's (or the run's) produced value.
 *
 * The gateway stores `output` / `final_answer` as JSON, and for the common case
 * the payload is a small object carrying the agent's answer under `text`
 * ({"text": "world"}). Showing that as a `<pre>` code block makes a one-word
 * answer look like machine output. So:
 *
 * - a plain string renders as prose;
 * - an object whose only key is `text` (or `result`/`content`) renders as prose
 *   too, because that is what the agent meant to say;
 * - anything else falls back to formatted JSON, since an arbitrary payload has
 *   no single right rendering — and the raw form stays available behind a
 *   disclosure rather than being lost.
 */

const PROSE_KEYS = ['text', 'result', 'content', 'answer'] as const;

function asProse(value: unknown): string | null {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    const keys = Object.keys(record);
    // Only unwrap when the payload is *just* the prose field — a richer object
    // (steps, usage, metadata) is information the reader should not lose.
    if (keys.length === 1) {
      const only = keys[0];
      if ((PROSE_KEYS as readonly string[]).includes(only)) {
        const inner = record[only];
        if (typeof inner === 'string') return inner;
      }
    }
  }
  return null;
}

export interface ValueBlockProps {
  value: unknown;
  /** Shown when the value is null or undefined. */
  empty: string;
  label: string;
}

export function ValueBlock({ value, empty, label }: ValueBlockProps) {
  const [showRaw, setShowRaw] = useState(false);

  if (value === null || value === undefined) {
    return <p className="m-0 text-sm text-muted-foreground">{empty}</p>;
  }

  const prose = asProse(value);
  const raw = typeof value === 'string' ? value : JSON.stringify(value, null, 2);

  if (prose !== null) {
    return (
      <div className="flex flex-col gap-1">
        <p className="m-0 whitespace-pre-wrap break-words text-sm leading-6">{prose}</p>
        {/* The same text, but only offer the raw view when it adds something. */}
        {raw !== prose ? (
          <details className="group">
            <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
              查看原始 JSON
            </summary>
            <div className="mt-1">
              <JsonBlock value={raw} maxHeight="16rem" />
            </div>
          </details>
        ) : null}
      </div>
    );
  }

  // No single prose reading exists: show the structure, not a faked sentence.
  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        className="self-start text-xs text-muted-foreground hover:text-foreground"
        onClick={() => setShowRaw((current) => !current)}
        aria-expanded={showRaw}
      >
        {showRaw ? '收起结构' : `查看${label}结构`}
      </button>
      {showRaw ? (
        <JsonBlock value={raw} maxHeight="18rem" />
      ) : (
        <p className="m-0 text-sm text-muted-foreground">
          该{label}是结构化数据，展开查看字段。
        </p>
      )}
    </div>
  );
}