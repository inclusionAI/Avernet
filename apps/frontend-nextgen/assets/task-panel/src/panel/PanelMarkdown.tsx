import React, { useMemo } from 'react';
import MarkdownIt from 'markdown-it';
import { SafeHtml } from '../rich-content/SafeHtml';
import { sanitizeMarkdownHtml } from '../rich-content/sanitizeHtml';
const markdown = new MarkdownIt({ html: false, breaks: true, linkify: true });
export function PanelMarkdown(props: { content?: string; children?: React.ReactNode; className?: string }) {
  const text = typeof props.content === 'string' ? props.content : typeof props.children === 'string' ? props.children : '';
  const html = useMemo(() => sanitizeMarkdownHtml(markdown.render(text)), [text]);
  return <SafeHtml html={html} className={props.className} />;
}
