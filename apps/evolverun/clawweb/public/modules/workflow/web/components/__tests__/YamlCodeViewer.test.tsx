import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import YamlCodeViewer, { formatYamlSnapshot, tokenizeYamlLine } from '../YamlCodeViewer'

describe('formatYamlSnapshot', () => {
  it('formats persisted JSON snapshots as multiline YAML', () => {
    const formatted = formatYamlSnapshot('{"id":"support","title":"支持","nodes":[]}')

    expect(formatted).toContain('id: support')
    expect(formatted).toContain('title: 支持')
    expect(formatted).toContain('nodes: []')
    expect(formatted).not.toContain('{"id"')
  })

  it('keeps outer JSON fields and large integer values when formatting snapshots', () => {
    const formatted = formatYamlSnapshot('{"id":"support","content":"title: inner","large":9007199254740993}')

    expect(formatted).toContain('content: "title: inner"')
    expect(formatted).toContain('large: 9007199254740993')
  })

  it('unwraps legacy content snapshots and formats their YAML', () => {
    expect(formatYamlSnapshot(JSON.stringify({ content: 'id: support\ntitle: 支持\n' }))).toContain('title: 支持')
  })

  it('can preserve a legacy wrapper when reviewing a complete history record', () => {
    const formatted = formatYamlSnapshot(JSON.stringify({ content: 'id: support\n' }), { unwrapLegacyContent: false })
    expect(formatted).toContain('content: |')
    expect(formatted).toContain('id: support')
  })

  it('preserves raw YAML comments and formatting for release-history fidelity', () => {
    const yaml = '# release rationale\nid: support\ntitle: "支持"\n'
    expect(formatYamlSnapshot(yaml)).toBe(yaml)
    expect(formatYamlSnapshot(JSON.stringify({ content: yaml }))).toBe(yaml)
  })

  it('handles JSON-encoded YAML and preserves unparseable text', () => {
    const yaml = 'id: support\ntitle: 支持\n'
    expect(formatYamlSnapshot(JSON.stringify(yaml))).toBe(yaml)
    expect(formatYamlSnapshot('{not valid json')).toBe('{not valid json')
  })
})

describe('tokenizeYamlLine', () => {
  it('classifies keys, strings, numbers, booleans, and comments', () => {
    expect(tokenizeYamlLine('enabled: true # feature flag')).toEqual([
      { text: 'enabled', type: 'key' },
      { text: ':', type: 'punctuation' },
      { text: ' ', type: 'plain' },
      { text: 'true', type: 'boolean' },
      { text: ' ', type: 'plain' },
      { text: '# feature flag', type: 'comment' },
    ])
    expect(tokenizeYamlLine('title: "支持"')).toEqual([
      { text: 'title', type: 'key' },
      { text: ':', type: 'punctuation' },
      { text: ' ', type: 'plain' },
      { text: '"支持"', type: 'string' },
    ])
  })
})

describe('YamlCodeViewer', () => {
  it('renders formatted YAML in a read-only syntax-colored code block', () => {
    render(<YamlCodeViewer value='{"enabled":true,"title":"支持"}' />)

    const viewer = screen.getByTestId('yaml-code-viewer')
    expect(viewer).toHaveTextContent('enabled: true')
    expect(viewer).toHaveTextContent('title: 支持')
    expect(viewer.querySelector('.text-sky-300')).toHaveTextContent('enabled')
    expect(viewer.querySelector('.text-violet-300')).toHaveTextContent('true')
  })
})
