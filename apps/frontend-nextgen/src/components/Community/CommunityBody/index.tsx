import { SafeHtml, sanitizeMarkdownHtml } from '@/components/Common/RichContent';
import MarkdownIt from 'markdown-it';
import { useMemo } from 'react';

// Keep the existing body string contract. Never enable raw HTML for user posts.
const markdown = new MarkdownIt({ html: false, breaks: true, linkify: false });

export function CommunityBody({ body }: { body: string }) {
  const html = useMemo(() => sanitizeMarkdownHtml(markdown.render(body)), [body]);
  return (
    <div className="markdown-body break-words text-sm leading-relaxed text-foreground">
      <SafeHtml html={html} />
    </div>
  );
}
