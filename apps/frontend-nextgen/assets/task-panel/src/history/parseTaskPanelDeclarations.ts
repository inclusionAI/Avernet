import MarkdownIt from 'markdown-it';
import type { TaskPanelDeclaration } from '../contracts';
const markdown = new MarkdownIt({ html: true });
const keys = new Set(['taskPanel.TaskLoopView', 'task-loop']);
/** Parse declarations as data only; code examples and scripts never run. */
export function parseTaskPanelDeclarations(content: string): TaskPanelDeclaration[] {
  const html: string[] = [];
  const collect = (tokens: ReturnType<typeof markdown.parse>) => {
    for (const token of tokens) {
      if (token.type === 'html_inline' || token.type === 'html_block') html.push(token.content);
      else if (token.children) collect(token.children);
    }
  };
  collect(markdown.parse(content, {}));
  const text = html.join('').replace(/<script\b[\s\S]*?<\/script\s*>/gi, '').replace(/<!--[\s\S]*?-->/g, '').replace(/<(pre|code)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, '');
  const declarations: TaskPanelDeclaration[] = [];
  const tag = /<AixUI\b((?:"[^"]*"|'[^']*'|[^'">])*)>/gi;
  let match: RegExpExecArray | null;
  while ((match = tag.exec(text))) {
    const attributes: Record<string, string> = {};
    const attr = /([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/g;
    let field: RegExpExecArray | null;
    while ((field = attr.exec(match[1]))) attributes[field[1]] = field[2] ?? field[3] ?? field[4];
    const component = attributes.component ?? attributes.panel;
    if (!keys.has(component) || (attributes.type && attributes.type !== 'panel') || attributes.cdn || attributes.entry) continue;
    if (!/\/\s*$/.test(match[1]) && !/<\/AixUI\s*>/i.test(text.slice(tag.lastIndex))) continue;
    try {
      const decode = (value: string) => value.replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'").replace(/&amp;/g, '&');
      const params = JSON.parse(decode(attributes.params ?? attributes.payload ?? '{}'));
      const tab = JSON.parse(decode(attributes.tab ?? '{}'));
      if (!params || typeof params.taskId !== 'string' || !params.taskId.trim() || !tab || Array.isArray(tab)) continue;
      declarations.push({ id: typeof tab.id === 'string' && tab.id ? tab.id : `task-${params.taskId}`,
        type: 'taskPanel.TaskLoopView', title: typeof tab.title === 'string' ? tab.title : '任务',
        closable: tab.closable !== false, defaultWidth: tab.defaultWidth ?? 420, params: { taskId: params.taskId } });
    } catch { /* Streaming or malformed history must not break the conversation. */ }
  }
  return declarations;
}
