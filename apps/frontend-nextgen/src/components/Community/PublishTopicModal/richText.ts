/** Serialize editable DOM to Markdown, preserving text instead of storing user HTML. */
export function communityEditorMarkdown(root: HTMLElement): string {
  const escapeText = (text: string) => text.replace(/([\\`*_[\]<>#~&.!+=|{}()-])/g, '\\$1');
  const serialize = {
    children(node: Node): string {
      let result = '';
      const nodes = Array.from(node.childNodes);
      for (let index = 0; index < nodes.length; index += 1) {
        const item = nodes[index];
        if (item instanceof HTMLElement && /^(STRONG|B|EM|I|S|DEL|U)$/.test(item.tagName)) {
          const merged = item.cloneNode(true) as HTMLElement;
          while (
            nodes[index + 1] instanceof HTMLElement &&
            (nodes[index + 1] as HTMLElement).tagName === item.tagName
          ) {
            const next = nodes[++index];
            Array.from(next.childNodes).forEach((child) => merged.appendChild(child.cloneNode(true)));
          }
          result += this.read(merged);
        } else {
          const block = item instanceof HTMLElement && /^(DIV|P|UL|OL|BLOCKQUOTE)$/.test(item.tagName);
          if (block && result && !result.endsWith('\n')) result += '\n';
          result += this.read(item);
        }
      }
      return result;
    },
    wrap(text: string, delimiter: string): string {
      const leading = text.match(/^\s*/)?.[0] ?? '';
      const trailing = text.match(/\s*$/)?.[0] ?? '';
      const content = text.trim();
      return content ? `${leading}${delimiter}${content}${delimiter}${trailing}` : text;
    },
    read(node: Node): string {
      if (node.nodeType === Node.TEXT_NODE) return escapeText(node.textContent ?? '');
      if (!(node instanceof HTMLElement)) return '';
      if (node.tagName === 'CODE') {
        const text = node.textContent ?? '';
        const longest = Math.max(0, ...(text.match(/`+/g) ?? []).map((run) => run.length));
        const delimiter = '`'.repeat(longest + 1);
        const padding =
          text.startsWith('`') || text.endsWith('`') || (/^ /.test(text) && / $/.test(text) && /[^ ]/.test(text))
            ? ' '
            : '';
        return text ? `${delimiter}${padding}${text}${padding}${delimiter}` : '';
      }
      const text = this.children(node);
      switch (node.tagName) {
        case 'HR': return '\n\n---\n\n';
        case 'H1': case 'H2': case 'H3': return `\n\n${'#'.repeat(Number(node.tagName[1]))} ${text}\n\n`;
        case 'BR':
          return '\n';
        case 'B':
        case 'STRONG':
          return this.wrap(text, '**');
        case 'I':
        case 'EM':
          return this.wrap(text, '*');
        case 'S':
        case 'DEL':
          return this.wrap(text, '~~');
        case 'U':
          return this.wrap(text, '__');
        case 'A': {
          const href = node.getAttribute('href') ?? '';
          return /^https?:\/\//i.test(href)
            ? `[${text}](${href
                .replace(/&/g, '&amp;')
                .replace(/[()\s]/g, (character) => `%${character.charCodeAt(0).toString(16).toUpperCase()}`)})`
            : text;
        }
        case 'LI':
          return `${node.parentElement?.tagName === 'OL' ? '1.' : '-'} ${text.trim()}\n`;
        case 'UL':
        case 'OL':
          return `\n${text}\n`;
        case 'BLOCKQUOTE':
          return `\n${text
            .trim()
            .split('\n')
            .map((line) => `> ${line}`)
            .join('\n')}\n`;
        case 'DIV':
        case 'P':
          return `${text}\n`;
        default:
          return text;
      }
    },
  };
  return serialize
    .children(root)
    .replace(/\u00a0/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trimEnd();
}
