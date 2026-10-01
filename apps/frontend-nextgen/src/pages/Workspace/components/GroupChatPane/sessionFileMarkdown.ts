// markdown-it 为 @alipay/bigfish 传递依赖（src/assets/TaskPanel/MarkdownCell 同款 import 先例）；
// html:false 禁止内联 HTML（XSS 安全），breaks 换行转 <br>，linkify 自动链接。
import { sanitizeMarkdownHtml } from '@/assets/TaskPanel/sanitizeHtml';
import { getFileExt } from '@/services/workspace/sessionFileUtils';
import MarkdownIt from 'markdown-it';

const md = new MarkdownIt({ html: false, breaks: true, linkify: true });

/** 是否 markdown 文件（富文本渲染；其余文本走等宽原文展示）。 */
export function isMarkdownFile(name: string): boolean {
  const ext = getFileExt(name);
  return ext === 'md' || ext === 'markdown';
}

/** 渲染 markdown 文本为净化 HTML（供 dangerouslySetInnerHTML）。 */
export function renderSessionFileMarkdown(content: string): string {
  return sanitizeMarkdownHtml(md.render(content));
}
