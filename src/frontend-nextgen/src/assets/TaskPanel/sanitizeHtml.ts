// @asset-migrated: teamclaw 自研资产
/**
 * Markdown HTML 净化：MarkdownCell 等用 markdown-it 渲染后的 HTML 串经 DOMPurify 白名单净化，
 * 剥离 script / 事件处理器(on*) / 危险协议(javascript:、vbscript:、data:)，消除 dangerouslySetInnerHTML 的 XSS 面。
 * 满足安全扫描 XssRiskJsFunctionUsing 误报条件 3（对渲染内容做安全防护/编码转义）。
 */
import DOMPurify from 'dompurify';

const MARKDOWN_HTML_ALLOWED_TAGS = [
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
  'em',
  'del',
  'ins',
  'sub',
  'sup',
  'code',
  'pre',
  'blockquote',
  'img',
  'span',
  'div',
  'table',
  'thead',
  'tbody',
  'tr',
  'th',
  'td',
];

const MARKDOWN_HTML_ALLOWED_ATTR = ['href', 'src', 'alt', 'title', 'class'];

/** 对 markdown-it 渲染产物做白名单净化，返回可安全注入 innerHTML 的 HTML 串。 */
export function sanitizeMarkdownHtml(html: string): string {
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: MARKDOWN_HTML_ALLOWED_TAGS,
    ALLOWED_ATTR: MARKDOWN_HTML_ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
  });
}
