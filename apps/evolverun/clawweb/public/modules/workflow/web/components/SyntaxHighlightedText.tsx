import type { ReactNode } from 'react'
import { SYNTAX_CLASSES, syntaxTokens, type SyntaxLanguage } from './syntaxHighlighting'

export type { SyntaxLanguage } from './syntaxHighlighting'

/**
 * A layout-free, lossless range renderer for virtualized editors. Its token budget
 * limits DOM for pathological dense lines; the remainder stays verbatim plain text.
 * The public full-text helper below does not impose this editor-specific budget.
 */
export function SyntaxHighlightedRange({ text, language, start, end, tokenBudget }: {
  text: string
  language: SyntaxLanguage
  start: number
  end: number
  tokenBudget: number
}) {
  const children: ReactNode[] = []
  let highlighted = 0
  for (const token of syntaxTokens(text, language, start, end)) {
    if (highlighted >= tokenBudget) {
      children.push(text.slice(token.start, end))
      break
    }
    const value = text.slice(token.start, token.end)
    if (token.kind === 'plain') children.push(value)
    else {
      children.push(<span key={token.start} data-syntax-kind={token.kind} className={SYNTAX_CLASSES[token.kind]}>{value}</span>)
      highlighted += 1
    }
  }
  return <>{children}</>
}

/** Inline syntax only: no layout, normalization, HTML injection, or large-document color cutoff. */
export default function SyntaxHighlightedText({ text, language = 'yaml' }: { text: string; language?: SyntaxLanguage }) {
  return <SyntaxHighlightedRange text={text} language={language} start={0} end={text.length} tokenBudget={Infinity} />
}
