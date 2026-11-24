import MarkdownIt from 'markdown-it';
const markdown = new MarkdownIt({ html: false });
/** Readable excerpts from the same Markdown string used by topic bodies. */
export function communityBodyPreview(body: string): string {
  const text: string[] = [];
  for (const block of markdown.parse(body, {})) {
    if (block.type !== 'inline') continue;
    text.push(
      (block.children ?? [])
        .map((token) => {
          if (token.type === 'text' || token.type === 'code_inline') return token.content;
          if (token.type === 'softbreak' || token.type === 'hardbreak') return ' ';
          if (token.type === 'image') return token.content;
          return '';
        })
        .join(''),
    );
  }
  return text.join(' ').replace(/\s+/g, ' ').trim();
}
