import MarkdownIt from 'markdown-it';
import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { sanitizeMarkdownHtml } from '@/components/Common/RichContent';

/**
 * A collapsed block of prose, rendered as Markdown once opened.
 *
 * The run log holds one of these per dispatch. Showing them expanded turns ten
 * agents into a wall of payloads, so each starts closed and the log reads as a
 * sequence; opening one shows the text as it was written — the gateway stores
 * instructions and answers as Markdown, and rendering that as a `<pre>` block
 * left every list and code span as literal punctuation.
 */

// `html: false`: the text came from a model or a definition, not from us, so it
// is parsed as Markdown and never as markup.
const markdown = new MarkdownIt({ html: false, breaks: true, linkify: false });

/** Indents each line so a multi-line value sits inside the quote marker. */
function asQuote(text: string): string {
  return text
    .split('\n')
    .map((line) => `> ${line}`)
    .join('\n');
}

export interface QuoteDisclosureProps {
  label: string;
  /** Shown next to the label while closed, e.g. a one-line preview. */
  preview?: string;
  value: string;
  /** Rendered instead of the Markdown when the value must stay verbatim. */
  mono?: boolean;
  tone?: 'default' | 'muted';
}

export function QuoteDisclosure({
  label,
  preview,
  value,
  mono = false,
  tone = 'default',
}: QuoteDisclosureProps) {
  const [open, setOpen] = useState(false);
  // Both kinds render as a Markdown quote so they share one look; `mono` puts a
  // fenced block inside it, which is what keeps an agent's output verbatim
  // (its own punctuation is data, not formatting) while still reading as a
  // block of text rather than a second style.
  const body = mono ? `\`\`\`\n${value.replace(/```/g, '``\\`')}\n\`\`\`` : value;
  const html = !open ? '' : sanitizeMarkdownHtml(markdown.render(asQuote(body)));

  return (
    <div className="flex flex-col">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className="flex min-w-0 items-center gap-1 self-start text-left text-[11px] text-muted-foreground hover:text-foreground"
      >
        {open ? (
          <ChevronDown className="size-3 shrink-0" aria-hidden />
        ) : (
          <ChevronRight className="size-3 shrink-0" aria-hidden />
        )}
        <span className="shrink-0 font-medium">{label}</span>
        {!open && preview ? (
          <span className="truncate text-muted-foreground/80">{preview}</span>
        ) : null}
      </button>
      {open ? (
        <div
          // No border of its own: the content is Markdown inside a `>` quote, and
          // `.markdown-body blockquote` already draws that marker. Adding one
          // here produced two rules down the same edge.
          className={[
            'markdown-body mt-1 max-h-64 overflow-auto rounded-r-md px-1 py-1 text-xs leading-6',
            tone === 'muted' ? 'text-muted-foreground' : 'text-foreground',
          ].join(' ')}
          // Sanitised through the shared allowlist, not trusted raw HTML.
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : null}
    </div>
  );
}