import { memo, useCallback, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEventHandler } from 'react'
import { SyntaxHighlightedRange } from './SyntaxHighlightedText'
import { syntaxLanguageForPath } from './syntaxHighlighting'

const LINE_HEIGHT = 22
const PADDING = 16
const OVERSCAN = 3
const TOKEN_BUDGET = 2048
const HighlightedRange = memo(SyntaxHighlightedRange)

// Both layers must use identical metrics, including tabs and ligatures. Tokens change color only.
const TEXT_STYLE: CSSProperties = {
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
  fontSize: 12,
  fontWeight: 400,
  fontStyle: 'normal',
  lineHeight: `${LINE_HEIGHT}px`,
  letterSpacing: 'normal',
  fontVariantLigatures: 'none',
  tabSize: 4,
  whiteSpace: 'pre',
  overflowWrap: 'normal',
  wordBreak: 'normal',
  textIndent: 0,
  textTransform: 'none',
  padding: PADDING,
  margin: 0,
  border: 0,
  boxSizing: 'border-box',
}

function lineOffsets(text: string): number[] {
  const offsets = [0]
  let index = text.indexOf('\n')
  while (index !== -1) {
    offsets.push(index + 1)
    index = text.indexOf('\n', index + 1)
  }
  return offsets
}

export default function PackFileCodeEditor({ path, value, onChange, disabled = false, ariaLabel = '文件内容', onKeyDown, layout = 'file' }: {
  path: string
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  ariaLabel?: string
  onKeyDown?: KeyboardEventHandler<HTMLTextAreaElement>
  /** Fill the remaining flex space without the standalone file editor's 430px minimum. */
  layout?: 'file' | 'fill'
}) {
  const input = useRef<HTMLTextAreaElement>(null)
  const previousPath = useRef(path)
  const [viewport, setViewport] = useState({ top: 0, left: 0, width: 0, height: 430 })
  const offsets = useMemo(() => lineOffsets(value), [value])
  const language = syntaxLanguageForPath(path)

  const syncViewport = useCallback(() => {
    const element = input.current
    if (!element) return
    const next = {
      top: element.scrollTop,
      left: element.scrollLeft,
      width: element.clientWidth,
      height: element.clientHeight || 430,
    }
    setViewport(current => current.top === next.top && current.left === next.left &&
      current.width === next.width && current.height === next.height ? current : next)
  }, [])

  useLayoutEffect(() => {
    const element = input.current
    if (element && previousPath.current !== path) {
      element.scrollTop = 0
      element.scrollLeft = 0
      previousPath.current = path
    }
    // Controlled replacements/deletions can clamp native scrolling without a user scroll event.
    syncViewport()
  }, [path, value, syncViewport])

  useLayoutEffect(() => {
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(syncViewport)
    if (input.current) observer?.observe(input.current)
    window.addEventListener('resize', syncViewport)
    return () => { observer?.disconnect(); window.removeEventListener('resize', syncViewport) }
  }, [syncViewport])

  const firstLine = Math.min(offsets.length - 1, Math.max(0, Math.floor((viewport.top - PADDING) / LINE_HEIGHT) - OVERSCAN))
  const lastLine = Math.min(offsets.length, firstLine + Math.ceil(viewport.height / LINE_HEIGHT) + OVERSCAN * 2 + 1)
  const start = offsets[firstLine]
  const end = offsets[lastLine] ?? value.length

  return <div className={`relative min-w-0 flex-1 focus-within:ring-2 focus-within:ring-inset focus-within:ring-ring/30 ${layout === 'fill' ? 'min-h-0 overflow-hidden' : 'h-full min-h-[430px]'} ${disabled ? 'bg-muted/40 opacity-60' : 'bg-background'}`}>
    <div
      data-testid="pack-file-highlight"
      aria-hidden="true"
      className="pointer-events-none absolute left-0 top-0 select-none overflow-hidden text-foreground"
      style={{ width: viewport.width || '100%', height: viewport.height }}
    >
      <pre style={{ ...TEXT_STYLE, position: 'absolute', top: 0, left: 0, width: 'max-content', transform: `translate(${-viewport.left}px, ${firstLine * LINE_HEIGHT - viewport.top}px)` }}>
        <HighlightedRange text={value} language={language} start={start} end={end} tokenBudget={TOKEN_BUDGET} />
      </pre>
    </div>
    <textarea
      ref={input}
      aria-label={ariaLabel}
      className="absolute inset-0 block h-full w-full resize-none overflow-auto bg-transparent text-transparent caret-foreground outline-none selection:bg-primary/20 selection:text-transparent disabled:cursor-not-allowed"
      style={TEXT_STYLE}
      wrap="off"
      spellCheck={false}
      autoCapitalize="off"
      autoCorrect="off"
      value={value}
      disabled={disabled}
      onChange={event => onChange(event.target.value)}
      onKeyDown={onKeyDown}
      onScroll={syncViewport}
    />
  </div>
}
