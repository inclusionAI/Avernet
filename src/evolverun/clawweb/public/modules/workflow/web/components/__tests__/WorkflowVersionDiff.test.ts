import { describe, expect, it } from 'vitest'
import { formatSpecForDiff, unifiedDiff } from '../WorkflowVersionDiff'

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
})
