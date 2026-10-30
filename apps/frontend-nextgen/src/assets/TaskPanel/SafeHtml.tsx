import { createElement, Fragment, type ReactNode, useMemo } from 'react';

const VOID_TAGS = new Set(['br', 'hr', 'img']);

const SAFE_HTML_TAGS = new Set([
  'a',
  'p',
  'br',
  'hr',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'ul',
  'ol',
  'li',
  'strong',
  'b',
  'em',
  'i',
  'u',
  's',
  'del',
  'ins',
  'sub',
  'sup',
  'small',
  'mark',
  'code',
  'pre',
  'blockquote',
  'img',
  'span',
  'div',
  'figure',
  'figcaption',
  'table',
  'thead',
  'tbody',
  'tr',
  'th',
  'td',
]);

function isSafeHref(value: string): boolean {
  return /^(?:https?:|mailto:|tel:|\/(?!\/)|\.\.?\/|\?|#)/i.test(value);
}

function isSafeImageSource(value: string): boolean {
  return /^(?:https?:|\/(?!\/)|\.\.?\/|\?)/i.test(value);
}

/** Exposed for deterministic node-type tests; component rendering remains private through SafeHtml. */
export function renderSafeNode(node: Node, key: string): ReactNode {
  if (node.nodeType === Node.TEXT_NODE) {
    return node.textContent ?? '';
  }
  if (node.nodeType !== Node.ELEMENT_NODE) {
    return null;
  }

  const element = node as HTMLElement;
  const tagName = element.tagName.toLowerCase();
  const children = Array.from(element.childNodes)
    .filter((child) => !(tagName === 'table' && child.nodeType === Node.TEXT_NODE && !child.textContent?.trim()))
    .map((child, index) => renderSafeNode(child, `${key}-${index}`));

  if (!SAFE_HTML_TAGS.has(tagName)) {
    return createElement(Fragment, { key }, children);
  }

  const props: Record<string, string> = { key };
  const title = element.getAttribute('title');
  if (title) props.title = title;

  if (tagName === 'a') {
    const href = element.getAttribute('href')?.trim();
    if (href && isSafeHref(href)) {
      props.href = href;
      props.rel = 'noopener noreferrer';
    }
  }

  if (tagName === 'img') {
    const src = element.getAttribute('src')?.trim();
    if (src && isSafeImageSource(src)) {
      props.src = src;
    }
    props.alt = element.getAttribute('alt') ?? '';
    props.loading = 'lazy';
  }

  return VOID_TAGS.has(tagName) ? createElement(tagName, props) : createElement(tagName, props, children);
}

/**
 * Renders sanitized HTML as a constrained React element tree instead of assigning a raw HTML string to the DOM.
 * The renderer keeps only the formatting tags needed by Markdown and release notes, and it validates all URL props.
 */
export function SafeHtml({ html, className }: { html: string; className?: string }) {
  const children = useMemo(() => {
    const document = new DOMParser().parseFromString(html, 'text/html');
    return Array.from(document.body.childNodes).map((node, index) => renderSafeNode(node, String(index)));
  }, [html]);

  return <div className={className}>{children}</div>;
}
