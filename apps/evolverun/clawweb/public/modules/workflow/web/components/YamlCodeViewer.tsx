import type { ReactNode } from 'react'
import { parse as parseYaml, stringify as stringifyYaml } from 'yaml'

interface YamlCodeViewerProps {
  /** A persisted spec_json value, legacy YAML wrapper, or raw YAML text. */
  value: unknown
  className?: string
  /** Keep legacy wrapper fields visible when reviewing a complete history record. */
  unwrapLegacyContent?: boolean
}

export type YamlTokenType =
  | 'plain'
  | 'key'
  | 'punctuation'
  | 'string'
  | 'number'
  | 'boolean'
  | 'null'
  | 'comment'
  | 'anchor'
  | 'tag'

export interface YamlToken {
  text: string
  type: YamlTokenType
}

const TOKEN_CLASSES: Record<YamlTokenType, string> = {
  plain: 'text-slate-200',
  key: 'text-sky-300',
  punctuation: 'text-slate-400',
  string: 'text-emerald-300',
  number: 'text-amber-300',
  boolean: 'text-violet-300',
  null: 'text-slate-500',
  comment: 'text-slate-500 italic',
  anchor: 'text-pink-300',
  tag: 'text-pink-300',
}

function readQuotedToken(line: string, start: number): number {
  const quote = line[start]
  let index = start + 1
  while (index < line.length) {
    if (line[index] === quote && (quote === "'" || line[index - 1] !== '\\')) return index + 1
    index += 1
  }
  return line.length
}

function readWordToken(line: string, start: number): number {
  let index = start
  while (index < line.length && !/[\s,:{}[\]]/.test(line[index])) index += 1
  return index
}

function isKeyToken(line: string, end: number): boolean {
  let next = end
  while (next < line.length && /\s/.test(line[next])) next += 1
  if (line[next] !== ':') return false
  const afterColon = line[next + 1]
  return afterColon == null || /\s/.test(afterColon) || ',}]'.includes(afterColon)
}

function valueTokenType(token: string): YamlTokenType {
  if (/^(?:true|false|yes|no|on|off)$/i.test(token)) return 'boolean'
  if (/^(?:null|~)$/i.test(token)) return 'null'
  if (/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(token)) return 'number'
  if (/^[&*]/.test(token)) return 'anchor'
  if (token.startsWith('!')) return 'tag'
  return 'plain'
}

/** Tokenize one YAML line without changing its text or relying on HTML injection. */
export function tokenizeYamlLine(line: string): YamlToken[] {
  const tokens: YamlToken[] = []
  let index = 0

  while (index < line.length) {
    const character = line[index]

    if (character === '#' && (index === 0 || /\s/.test(line[index - 1]))) {
      tokens.push({ text: line.slice(index), type: 'comment' })
      break
    }

    if (/\s/.test(character)) {
      const start = index
      while (index < line.length && /\s/.test(line[index])) index += 1
      tokens.push({ text: line.slice(start, index), type: 'plain' })
      continue
    }

    if (character === '"' || character === "'") {
      const end = readQuotedToken(line, index)
      tokens.push({ text: line.slice(index, end), type: 'string' })
      index = end
      continue
    }

    if ('-:?,{}[]|>'.includes(character)) {
      tokens.push({ text: character, type: 'punctuation' })
      index += 1
      continue
    }

    const end = readWordToken(line, index)
    const text = line.slice(index, end)
    tokens.push({ text, type: isKeyToken(line, end) ? 'key' : valueTokenType(text) })
    index = end === index ? index + 1 : end
  }

  return tokens
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

/**
 * Convert persisted workflow snapshots into readable YAML.
 *
 * Older snapshots can be raw YAML, JSON objects, JSON-encoded YAML, or the
 * legacy `{ content: string }` wrapper. JSON objects are serialized as readable
 * YAML; existing YAML text stays lossless so release comments and quoting remain
 * available to reviewers.
 */
export function formatYamlSnapshot(value: unknown, options: { unwrapLegacyContent?: boolean } = {}): string {
  const original = typeof value === 'string' ? value : undefined
  const unwrapLegacyContent = options.unwrapLegacyContent !== false
  let current: unknown = value

  for (let attempt = 0; attempt < 4; attempt += 1) {
    if (typeof current === 'string') {
      const text = current
      const trimmed = text.trim()
      if (!trimmed) return ''

      // JSON objects/arrays are valid YAML too. Parsing them with `intAsBigInt`
      // keeps large persisted integers lossless while converting the snapshot to
      // readable multiline YAML. Existing block/scalar YAML stays verbatim so
      // comments, quoting, and anchors remain faithful to the release record.
      if (!/^[\[{"]/.test(trimmed)) return text
      try {
        current = parseYaml(trimmed, { intAsBigInt: true }) as unknown
        continue
      } catch {
        return text
      }
    }

    if (unwrapLegacyContent && isRecord(current) && Object.keys(current).length === 1 && typeof current.content === 'string') {
      return current.content
    }

    try {
      return stringifyYaml(current, { lineWidth: 0 })
    } catch {
      return original ?? String(current)
    }
  }

  return original ?? String(current)
}

function renderTokens(tokens: YamlToken[]): ReactNode[] {
  return tokens.map((token, index) => (
    <span key={`${token.type}-${index}`} className={TOKEN_CLASSES[token.type]}>
      {token.text}
    </span>
  ))
}

export default function YamlCodeViewer({ value, className = '', unwrapLegacyContent = true }: YamlCodeViewerProps) {
  const yamlText = formatYamlSnapshot(value, { unwrapLegacyContent })
  const lines = yamlText.split('\n')

  return (
    <div data-testid="yaml-code-viewer" className={`min-h-0 flex-1 overflow-auto bg-slate-950 p-5 ${className}`}>
      <pre className="m-0 min-w-max whitespace-pre font-mono text-xs leading-relaxed">
        <code>
          {lines.map((line, index) => (
            <span key={index} className="block min-h-[1.25rem]">
              {renderTokens(tokenizeYamlLine(line))}
            </span>
          ))}
        </code>
      </pre>
    </div>
  )
}
