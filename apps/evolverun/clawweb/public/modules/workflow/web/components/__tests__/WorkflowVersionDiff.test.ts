import { describe, expect, it } from 'vitest'
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
    expect(formatSpecForDiff(JSON.stringify({ content: yaml }))).toBe(yaml)
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

  it('removes editor-enriched fields before comparing a release with the current spec', () => {
    const deployed = formatSpecForDiff(JSON.stringify({ id: 'support', title: '支持', nodes: [], version: 2 }))
    const current = formatCurrentSpecForDiff({ id: 'support', title: '支持', nodes: [], version: 2, updatedAt: 123, facade: { command: 'support' } })

    expect(specsEqual(deployed, current)).toBe(true)
    expect(unifiedDiff(deployed, current).filter((line) => line.type === 'add' || line.type === 'del')).toEqual([])
  })
})
