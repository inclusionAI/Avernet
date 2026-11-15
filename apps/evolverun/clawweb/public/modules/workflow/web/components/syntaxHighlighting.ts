import { tokenizeYamlLine, type YamlTokenType } from './YamlCodeViewer'

export type SyntaxLanguage = 'yaml' | 'json' | 'python' | 'text'
export type SyntaxKind = YamlTokenType | 'keyword'
export type SyntaxToken = { start: number; end: number; kind: SyntaxKind }

// Color only: weight, italics and other font changes would misalign an input overlay.
export const SYNTAX_CLASSES: Record<SyntaxKind, string> = {
  plain: 'text-foreground',
  key: 'text-primary',
  punctuation: 'text-muted-foreground',
  string: 'text-success',
  number: 'text-primary',
  boolean: 'text-primary',
  null: 'text-muted-foreground',
  comment: 'text-muted-foreground',
  anchor: 'text-primary',
  tag: 'text-primary',
  keyword: 'text-primary',
}

export function syntaxLanguageForPath(path: string): SyntaxLanguage {
  switch (path.split('/').pop()?.split('.').pop()?.toLowerCase()) {
    case 'yaml': case 'yml': return 'yaml'
    case 'json': return 'json'
    case 'py': case 'pyw': return 'python'
    default: return 'text'
  }
}

const PYTHON_KEYWORDS = new Set([
  'and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue', 'def', 'del',
  'elif', 'else', 'except', 'finally', 'for', 'from', 'global', 'if', 'import', 'in',
  'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while',
  'with', 'yield', 'match', 'case',
])

function stringEnd(text: string, quoteStart: number, triple: boolean): number {
  const quote = text[quoteStart]
  const delimiter = triple ? quote.repeat(3) : quote
  let index = quoteStart + delimiter.length
  while (index < text.length) {
    if (text[index] === '\\') { index += 2; continue }
    if (text.startsWith(delimiter, index)) return index + delimiter.length
    if (!triple && (text[index] === '\n' || text[index] === '\r')) return index
    index += 1
  }
  return text.length
}

function* yamlTokens(text: string, start: number, end: number): Generator<SyntaxToken> {
  let offset = start
  while (offset < end) {
    const newline = text.indexOf('\n', offset)
    const lineEnd = newline < 0 ? end : Math.min(newline, end)
    // Bound temporary tokenizer allocations even for a token-dense, megabyte-long line.
    // Only pathological long lines are chunked; normal lines use the existing lexer verbatim.
    while (offset < lineEnd) {
      const chunkEnd = Math.min(offset + 16_384, lineEnd)
      for (const token of tokenizeYamlLine(text.slice(offset, chunkEnd))) {
        const next = offset + token.text.length
        yield { start: offset, end: next, kind: token.type }
        offset = next
      }
    }
    if (offset < end) {
      yield { start: offset, end: offset + 1, kind: 'plain' }
      offset += 1
    }
  }
}

/** Streaming lexical highlighting, not parsing/formatting; unfinished edits remain valid input. */
export function* syntaxTokens(text: string, language: SyntaxLanguage, start = 0, end = text.length): Generator<SyntaxToken> {
  if (language === 'text') { yield { start, end, kind: 'plain' }; return }
  if (language === 'yaml') { yield* yamlTokens(text, start, end); return }

  const python = language === 'python'
  const pattern = python
    ? /#[^\r\n]*|[rRuUbBfF]{0,2}["']|(?:0[xX][\da-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?[\d_]+)?)[jJ]?|[A-Za-z_]\w*|[^\w\s]/g
    : /"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\b(?:true|false|null)\b|[{}\[\],:]/g
  // Scan from the beginning to retain multiline Python string context above the viewport.
  let cursor = 0
  let match: RegExpExecArray | null
  while ((match = pattern.exec(text)) && match.index < end) {
    const offset = match.index
    if (offset > cursor && offset > start) yield { start: Math.max(start, cursor), end: Math.min(offset, end), kind: 'plain' }
    const word = match[0]
    let tokenEnd = pattern.lastIndex
    let kind: SyntaxKind = 'plain'
    if (python && word.startsWith('#')) kind = 'comment'
    else if (word.endsWith('"') || word.endsWith("'")) {
      const quoteStart = tokenEnd - 1
      const triple = python && text.startsWith(text[quoteStart].repeat(3), quoteStart)
      tokenEnd = stringEnd(text, quoteStart, triple)
      pattern.lastIndex = tokenEnd
      kind = 'string'
      if (!python) {
        let next = tokenEnd
        while (next < text.length && /\s/.test(text[next])) next += 1
        if (text[next] === ':') kind = 'key'
      }
    } else if (/^-?[\d.]/.test(word)) kind = 'number'
    else if (python ? word === 'True' || word === 'False' : word === 'true' || word === 'false') kind = 'boolean'
    else if (python ? word === 'None' : word === 'null') kind = 'null'
    else if (python && PYTHON_KEYWORDS.has(word)) kind = 'keyword'
    else if (/^[^\w\s]/.test(word)) kind = 'punctuation'
    if (tokenEnd > start) yield { start: Math.max(offset, start), end: Math.min(tokenEnd, end), kind }
    cursor = tokenEnd
    if (cursor >= end) return
  }
  if (cursor < end) yield { start: Math.max(cursor, start), end, kind: 'plain' }
}
