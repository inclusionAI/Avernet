/**
 * Read-only JSON, coloured by token.
 *
 * The raw view exists so a reader can see the exact payload; uncoloured, a
 * long object is one grey wall and the key they were looking for is not
 * findable. Highlighting happens by re-tokenising the pretty-printed text
 * rather than by walking the value, so the colours and the characters on screen
 * can never disagree.
 *
 * No editor dependency: this is a display, not a field, and a CodeMirror
 * instance per expanded block would cost far more than it returns.
 */

type TokenKind = 'key' | 'string' | 'number' | 'literal' | 'punct';

interface Token {
  text: string;
  kind: TokenKind;
}

const TOKEN_CLASS: Record<TokenKind, string> = {
  key: 'text-sky-700 dark:text-sky-300',
  string: 'text-emerald-700 dark:text-emerald-300',
  number: 'text-amber-700 dark:text-amber-300',
  literal: 'text-purple-700 dark:text-purple-300',
  punct: 'text-muted-foreground',
};

/**
 * Splits pretty-printed JSON into key/string/number/literal/punctuation runs.
 *
 * Deliberately a scanner and not `JSON.parse`: the input may be a truncated or
 * hand-edited string, and the caller still wants it shown.
 */
export function tokenizeJson(source: string): Token[] {
  const tokens: Token[] = [];
  const push = (text: string, kind: TokenKind) => {
    if (!text) return;
    const last = tokens[tokens.length - 1];
    // Merge neighbouring punctuation so the DOM stays a handful of spans.
    if (last && last.kind === kind && kind === 'punct') last.text += text;
    else tokens.push({ text, kind });
  };

  let index = 0;
  while (index < source.length) {
    const char = source[index];

    // Whitespace rides with the preceding token's gap, i.e. is punctuation.
    if (/\s/.test(char)) {
      let end = index;
      while (end < source.length && /\s/.test(source[end])) end += 1;
      push(source.slice(index, end), 'punct');
      index = end;
      continue;
    }

    if (char === '"') {
      let end = index + 1;
      while (end < source.length) {
        if (source[end] === '\\') end += 2;
        else if (source[end] === '"') {
          end += 1;
          break;
        } else end += 1;
      }
      const text = source.slice(index, end);
      // A quoted run followed (after optional space) by ':' is a key.
      let look = end;
      while (look < source.length && /\s/.test(source[look])) look += 1;
      push(text, source[look] === ':' ? 'key' : 'string');
      index = end;
      continue;
    }

    if (/[-\d.]/.test(char)) {
      let end = index;
      while (end < source.length && /[-\d.eE+]/.test(source[end])) end += 1;
      push(source.slice(index, end), 'number');
      index = end;
      continue;
    }

    if (source.startsWith('true', index) || source.startsWith('null', index)) {
      const word = source.startsWith('true', index) ? 'true' : 'null';
      push(word, 'literal');
      index += word.length;
      continue;
    }
    if (source.startsWith('false', index)) {
      push('false', 'literal');
      index += 5;
      continue;
    }

    push(char, 'punct');
    index += 1;
  }

  return tokens;
}

export function JsonBlock({ value, maxHeight = '18rem' }: { value: string; maxHeight?: string }) {
  return (
    <pre
      className="m-0 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted p-3 font-mono text-[11px] leading-5"
      style={{ maxHeight }}
    >
      <code>
        {tokenizeJson(value).map((token, index) => (
          <span key={index} className={TOKEN_CLASS[token.kind]}>
            {token.text}
          </span>
        ))}
      </code>
    </pre>
  );
}