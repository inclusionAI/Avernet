import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SyntaxHighlightedText from '../SyntaxHighlightedText'
import * as yamlViewer from '../YamlCodeViewer'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

function tokens(container: HTMLElement, kind: string) {
  return Array.from(container.querySelectorAll(`[data-syntax-kind="${kind}"]`), node => node.textContent)
}

describe('SyntaxHighlightedText', () => {
  it('defaults to lossless YAML using the existing tokenizer and semantic inline tokens', () => {
    const tokenize = vi.spyOn(yamlViewer, 'tokenizeYamlLine')
    const text = 'name: "测试"\nenabled: true # keep\ncount: 42\nmissing: null\n'
    const { container } = render(<SyntaxHighlightedText text={text} />)
    expect(tokenize).toHaveBeenCalled()
    expect(container.textContent).toBe(text)
    expect(tokens(container, 'key')).toContain('name')
    expect(tokens(container, 'string')).toContain('"测试"')
    expect(tokens(container, 'boolean')).toContain('true')
    expect(tokens(container, 'number')).toContain('42')
    expect(tokens(container, 'comment')).toContain('# keep')
    expect(container.querySelector('[data-syntax-kind="key"]')).toHaveClass('text-primary')
    expect(container.querySelector('[data-syntax-kind="string"]')).toHaveClass('text-success')
    expect(container.querySelector('[data-syntax-kind="comment"]')).toHaveClass('text-muted-foreground')
    expect(container.querySelector('pre, div, p, br, .block')).toBeNull()
  })

  it.each(['yaml', 'python', 'json', 'text'] as const)('preserves every character including HTML, BOM, tabs, CRLF and empty lines (%s)', language => {
    const text = '\ufeff# 原文\r\n\t<img src=x onerror="alert(1)"> & <script>bad()</script>\n\n  \n'
    const { container } = render(<SyntaxHighlightedText text={text} language={language} />)
    expect(container.textContent).toBe(text)
    expect(container.querySelector('img, script')).toBeNull()
    expect(container.querySelector('pre, div, br')).toBeNull()
  })

  it('highlights Python keywords, numbers, booleans, comments and multiline strings', () => {
    const text = 'import os\n# screenshot\ndef capture(page):\n    """doc\n# not a comment\n    """\n    enabled = True\n    return "<img>" if enabled else 42\n'
    const { container } = render(<SyntaxHighlightedText text={text} language="python" />)
    expect(container.textContent).toBe(text)
    expect(tokens(container, 'keyword')).toEqual(expect.arrayContaining(['import', 'def', 'return', 'if', 'else']))
    expect(tokens(container, 'boolean')).toContain('True')
    expect(tokens(container, 'number')).toContain('42')
    expect(tokens(container, 'comment')).toEqual(['# screenshot'])
    expect(tokens(container, 'string').join('')).toContain('# not a comment')
  })

  it('highlights JSON keys, escaped strings, numbers and literals without pretty-printing', () => {
    const text = '{"name":"a\\\"b", "count":-1.25e+2,"enabled":false,"empty":null}\n'
    const { container } = render(<SyntaxHighlightedText text={text} language="json" />)
    expect(container.textContent).toBe(text)
    expect(tokens(container, 'key')).toContain('"name"')
    expect(tokens(container, 'string')).toContain('"a\\\"b"')
    expect(tokens(container, 'number')).toContain('-1.25e+2')
    expect(tokens(container, 'boolean')).toContain('false')
    expect(tokens(container, 'null')).toContain('null')
  })

  it('keeps several thousand ordinary YAML lines highlighted, including the last line', () => {
    const text = Array.from({ length: 3000 }, (_, index) => `key_${index}: true # flag\n`).join('')
    const { container } = render(<SyntaxHighlightedText text={text} />)
    expect(container.textContent).toBe(text)
    expect(tokens(container, 'key')).toHaveLength(3000)
    expect(tokens(container, 'key').at(-1)).toBe('key_2999')
  })

  it('accepts empty and unfinished input without parsing or rewriting it', () => {
    const { container, rerender } = render(<SyntaxHighlightedText text="" />)
    expect(container.textContent).toBe('')
    for (const text of ['name: "unfinished', 'x = """unfinished\n# text', '{"x": [']) {
      rerender(<SyntaxHighlightedText text={text} language="python" />)
      expect(container.textContent).toBe(text)
    }
  })
})
