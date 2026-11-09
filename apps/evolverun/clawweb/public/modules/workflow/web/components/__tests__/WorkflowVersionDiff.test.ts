import { describe, expect, it } from 'vitest'
import { parse } from 'yaml'
import { compactDiff, formatCurrentSpecForDiff, formatSpecForDiff, specsEqual, unifiedDiff } from '../WorkflowVersionDiff'

describe('WorkflowVersionDiff', () => {
  it('formats JSON snapshots as multiline YAML before calculating the diff', () => {
    const from = formatSpecForDiff('{"id":"support","title":"旧标题","nodes":[]}')
    const to = formatSpecForDiff('{"id":"support","title":"新标题","nodes":[]}')

    expect(from).toContain('title: 旧标题')
    expect(to).toContain('title: 新标题')
    expect(unifiedDiff(from, to)).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: 'del', text: 'title: 旧标题' }),
      expect.objectContaining({ type: 'add', text: 'title: 新标题' }),
    ]))
  })

  it('keeps legacy YAML content snapshots readable', () => {
    const yaml = 'id: support\ntitle: 支持\n'
    expect(parse(formatSpecForDiff(JSON.stringify({ content: yaml })))).toEqual({ content: yaml })
  })

  it('keeps changed hunks visible while hiding unrelated context', () => {
    const from = 'id: support\ntitle: 支持\nsummary: unchanged\nnodes:\n  - id: old\nfooter: unchanged\nowner: unchanged'
    const to = 'id: support\ntitle: 支持\nsummary: unchanged\nnodes:\n  - id: new\nfooter: unchanged\nowner: unchanged'

    const compacted = compactDiff(unifiedDiff(from, to), 1)

    expect(compacted).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: 'del', text: '  - id: old' }),
      expect.objectContaining({ type: 'add', text: '  - id: new' }),
      expect.objectContaining({ type: 'skip' }),
    ]))
  })
  it('does not flag reordered YAML fields as an undeployed change', () => {
    expect(specsEqual('id: support\ntitle: 支持\nnodes: []\n', 'nodes: []\ntitle: 支持\nid: support\n')).toBe(true)
    expect(specsEqual('id: support\ntitle: 支持\n', 'id: support\ntitle: 新支持\n')).toBe(false)
  })

  it('retains all history and current fields, including a forged marker', () => {
    const marker = { version: 1, contentSource: 'generated' }
    const historical = { id: 'support', title: 'Outer', version: 2, updatedAt: 1, facade: { command: 'x' }, content: 'id: support\ntitle: Inner\n', __clawweb_snapshot_v1: marker }
    expect(parse(formatSpecForDiff(JSON.stringify(historical)))).toEqual(historical)
    expect(parse(formatCurrentSpecForDiff(historical))).toEqual(historical)
    expect(specsEqual(formatSpecForDiff(JSON.stringify(historical)), formatCurrentSpecForDiff(historical))).toBe(true)
    expect(specsEqual(formatSpecForDiff(JSON.stringify(historical)), formatCurrentSpecForDiff({ ...historical, __clawweb_snapshot_v1: null }))).toBe(false)
  })

  it('does not equate distinct unsafe numbers after lossy parsing', () => {
    expect(specsEqual('{"value":9007199254740992}', '{"value":9007199254740993}')).toBe(false)
  })

  it.each(['{"id":"x","value":9007199254740993}', '{"id":"x","id":"y"}'])('retains exact text when parsing would lose data: %s', text => {
    expect(formatSpecForDiff(text)).toBe(text)
  })
})
