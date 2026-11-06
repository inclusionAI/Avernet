import { createElement, type CSSProperties, Fragment, type ReactNode, useMemo } from 'react';

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

/** richText 模式样式白名单：仅颜色与字号（发布说明运营富文本）；React 内联样式序列化，无注入面。 */
const RICH_TEXT_COLOR_RE =
  /^(?:#[0-9a-f]{3}|#[0-9a-f]{6}|red|orange|yellow|green|blue|purple|pink|brown|gold|gray|grey|black|white|cyan|magenta)$/i;
const RICH_TEXT_FONT_SIZE_RE = /^\d+(?:\.\d+)?(?:px|em|rem|%)$/i;

/** richText 模式下解析 style 属性：仅保留白名单内的颜色/字号取值，其余静默丢弃。 */
function parseRichTextStyle(styleAttr: string | null): CSSProperties | null {
  if (!styleAttr) return null;
  let style: CSSProperties | undefined;
  for (const pair of styleAttr.split(';')) {
    const idx = pair.indexOf(':');
    if (idx < 0) continue;
    const prop = pair.slice(0, idx).trim().toLowerCase();
    const value = pair.slice(idx + 1).trim();
    if (prop === 'color' && RICH_TEXT_COLOR_RE.test(value)) {
      style = style ?? {};
      style.color = value;
    } else if (prop === 'font-size' && RICH_TEXT_FONT_SIZE_RE.test(value)) {
      style = style ?? {};
      style.fontSize = value;
    }
  }
  return style ?? null;
}

/** richText 模式下剪除纯空白标题占位行；含图片的标题保留。 */
function isBlankHeading(element: HTMLElement): boolean {
  return (
    /^h[1-6]$/.test(element.tagName.toLowerCase()) &&
    !(element.textContent ?? '').replace(/\u00a0/g, ' ').trim() &&
    !element.querySelector('img')
  );
}

function renderSafeNode(node: Node, key: string, richText: boolean): ReactNode {
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
    .map((child, index) => renderSafeNode(child, `${key}-${index}`, richText));

  if (!SAFE_HTML_TAGS.has(tagName)) {
    return createElement(Fragment, { key }, children);
  }

  if (richText && isBlankHeading(element)) {
    return null;
  }

  const props: {
    key: string;
    title?: string;
    href?: string;
    rel?: string;
    target?: string;
    src?: string;
    alt?: string;
    loading?: string;
    style?: CSSProperties;
  } = { key };
  const title = element.getAttribute('title');
  if (title) props.title = title;

  if (tagName === 'a') {
    const href = element.getAttribute('href')?.trim();
    if (href && isSafeHref(href)) {
      props.href = href;
      props.rel = 'noopener noreferrer';
      if (richText) props.target = '_blank';
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

  if (richText) {
    const style = parseRichTextStyle(element.getAttribute('style'));
    if (style) props.style = style;
  }

  return VOID_TAGS.has(tagName) ? createElement(tagName, props) : createElement(tagName, props, children);
}

/**
 * Renders sanitized HTML as a constrained React element tree instead of assigning a raw HTML string to the DOM.
 * The renderer keeps only the formatting tags needed by Markdown and release notes, and it validates all URL props.
 * richText mode (release notes) additionally renders whitelisted color/font-size styles,
 * opens safe links in a new tab, and prunes whitespace-only placeholder headings.
 */
export function SafeHtml({ html, className, richText }: { html: string; className?: string; richText?: boolean }) {
  const children = useMemo(() => {
    const document = new DOMParser().parseFromString(html, 'text/html');
    return Array.from(document.body.childNodes).map((node, index) => renderSafeNode(node, String(index), !!richText));
  }, [html, richText]);

  return <div className={className}>{children}</div>;
}
