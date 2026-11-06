import { createHash, webcrypto } from 'node:crypto'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { originalActiveDraftBaseline, readEditorWorkflowSource, type TrustedActiveHistoryReader } from '../editorWorkflowSource'

const authored = { id: 'flow', title: 'Original', nodes: [
  { id: 'start', title: 'Start', executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', title: 'End', executor: { type: 'end' }, dependsOn: ['start'] },
], content: 'id: nested\nnodes: []\n', custom: { keep: [false, 0, null] } }
const yaml = stringify(authored)
// A non-content marker remains eligible for the independently trusted reader.
const { content: _content, ...withoutContent } = authored
const archived = JSON.stringify({ ...withoutContent, __clawweb_snapshot_v1: { version: 1, contentSource: 'generated' } })
const token = (text: string) => createHash('sha256').update(JSON.stringify([
  'active-workflow-baseline-v1', 'flow', 'pack', '3', '7', 'v7', 'deploy', text,
])).digest('hex')
const baseActive = { deployNumber: 3, version: 7, isActive: true, packId: 'pack',
  tagName: 'v7', action: 'deploy', activeToken: token(archived) }

function setup() {
  vi.stubGlobal('crypto', webcrypto)
  let currentActive: Record<string, unknown> = baseActive
  let draftExists = false
  let specJson = archived
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    expect(!init?.method || init.method === 'GET').toBe(true)
    if (url === '/api/workflows/flow') return draftExists
      ? new Response(JSON.stringify(authored))
      : new Response(JSON.stringify({ code: 'WORKFLOW_NOT_FOUND', workflowId: 'flow' }), { status: 404 })
    if (url === '/api/workflows/flow/editor-draft') return new Response(JSON.stringify({ workflowId: 'flow', packId: 'pack', specJson: JSON.stringify(authored), draftToken: 'a'.repeat(64) }))
    if (url.includes('/history?')) return new Response(JSON.stringify({ workflowId: 'flow', history: [], active: currentActive }))
    if (url.endsWith('/by-deploy/3')) return new Response(JSON.stringify({ workflowId: 'flow', deployNumber: 3, version: 7, tagName: 'v7', action: 'deploy', specJson }))
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, setActive: (value: Record<string, unknown>) => { currentActive = value },
    setDraft: () => { draftExists = true }, setSnapshot: (value: string) => { specJson = value } }
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('active history recovery via an independently trusted host reader', () => {
  it('recovers a non-content marker through the reader with its exact row baseline, without writing', async () => {
    const { fetchMock } = setup()
    const reader: TrustedActiveHistoryReader = vi.fn(async identity => {
      expect(Object.isFrozen(identity)).toBe(true)
      expect(identity).toEqual({ workflowId: 'flow', packId: 'pack', deployNumber: 3, version: 7,
        activeToken: token(archived), tagName: 'v7', action: 'deploy' })
      return { ...identity, workflowText: yaml }
    })
    const source = await readEditorWorkflowSource('flow', { readTrustedActiveHistory: reader })
    expect(source).toEqual({ spec: authored, databaseSpec: null, activeDeployNumber: 3 })
    expect(originalActiveDraftBaseline(source.spec)).toMatchObject({ activeToken: token(archived), specJson: archived })
    expect(fetchMock).toHaveBeenCalledTimes(7)
  })
  it('preserves an authored marker and YAML-looking content from the trusted full document', async () => {
    setup()
    const doc = { ...authored, __clawweb_snapshot_v1: { version: 1, contentSource: 'generated' } }
    const source = await readEditorWorkflowSource('flow', { readTrustedActiveHistory: async identity => ({ ...identity, workflowText: stringify(doc) }) })
    expect(source.spec).toEqual(doc)
  })
  it('defaults to ambiguity and never calls the reader for an invalid active token', async () => {
    const env = setup()
    const reader = vi.fn(async () => null)
    await expect(readEditorWorkflowSource('flow')).rejects.toThrow('无法验证来源')
    env.setActive({ ...baseActive, activeToken: 'f'.repeat(64) })
    await expect(readEditorWorkflowSource('flow', { readTrustedActiveHistory: reader })).rejects.toThrow('校验不匹配')
    expect(reader).not.toHaveBeenCalled()
  })
  it.each(['workflowId', 'packId', 'deployNumber', 'version', 'activeToken', 'tagName', 'action'] as const)('rejects mismatched %s', async key => {
    setup()
    await expect(readEditorWorkflowSource('flow', { readTrustedActiveHistory: async identity => ({
      ...identity, [key]: 'wrong', workflowText: yaml,
    }) })).rejects.toThrow('无法验证来源')
  })
  it.each([null, 'bad', 'id: other\ntitle: Wrong\nnodes: []', 'id: flow\ntitle: A\nnodes: []\ncustom: 9007199254740993',
    'id: flow\ntitle: A\nnodes: []\ncontent: !unknown data'])('rejects unavailable, invalid, mismatched or lossy trusted result: %s', async text => {
    setup()
    await expect(readEditorWorkflowSource('flow', { readTrustedActiveHistory: async identity => text === null ? null : ({ ...identity, workflowText: text }) })).rejects.toThrow()
  })
  it('rejects an active switch while the trusted source is being read', async () => {
    const env = setup()
    await expect(readEditorWorkflowSource('flow', { readTrustedActiveHistory: async identity => {
      env.setActive({ ...baseActive, deployNumber: 4 })
      return { ...identity, workflowText: yaml }
    } })).rejects.toThrow('生效版本已变化')
  })
  it('rejects a draft appearing during the trusted read', async () => {
    const env = setup()
    await expect(readEditorWorkflowSource('flow', { readTrustedActiveHistory: async identity => {
      env.setDraft()
      return { ...identity, workflowText: yaml }
    } })).rejects.toThrow('草稿已变化')
  })
  it('rejects a draft created during the history recheck', async () => {
    const env = setup()
    const prior = env.fetchMock.getMockImplementation()!
    let historyReads = 0
    env.fetchMock.mockImplementation(async (url, init) => {
      if (url.includes('/history?') && ++historyReads === 2) env.setDraft()
      return prior(url, init)
    })
    await expect(readEditorWorkflowSource('flow', { readTrustedActiveHistory: async identity => ({ ...identity, workflowText: yaml }) }))
      .rejects.toThrow('草稿已变化')
  })
  it('prefers an existing draft and never invokes the trusted reader', async () => {
    const env = setup()
    env.setDraft()
    const reader = vi.fn(async () => null)
    const source = await readEditorWorkflowSource('flow', { readTrustedActiveHistory: reader })
    expect(source).toMatchObject({ spec: authored, databaseSpec: authored, activeDeployNumber: null })
    expect(reader).not.toHaveBeenCalled()
    expect(env.fetchMock).toHaveBeenCalledTimes(2)
  })
  it('does not use the trusted reader without a verifiable active token', async () => {
    const env = setup()
    env.setActive({ ...baseActive, activeToken: undefined })
    const reader = vi.fn(async () => null)
    await expect(readEditorWorkflowSource('flow', { readTrustedActiveHistory: reader })).rejects.toThrow('无法验证来源')
    expect(reader).not.toHaveBeenCalled()
  })
  it('rejects a changed row with the same deployment number', async () => {
    const env = setup()
    await expect(readEditorWorkflowSource('flow', { readTrustedActiveHistory: async identity => {
      env.setActive({ ...baseActive, activeToken: 'f'.repeat(64) })
      return { ...identity, workflowText: yaml }
    } })).rejects.toThrow('生效版本已变化')
  })
  it('does not invoke the reader to bypass an identity-mismatched history', async () => {
    const env = setup()
    const bad = JSON.stringify({ ...authored, id: 'other' })
    env.setSnapshot(bad)
    env.setActive({ ...baseActive, activeToken: token(bad) })
    const reader = vi.fn(async () => null)
    await expect(readEditorWorkflowSource('flow', { readTrustedActiveHistory: reader })).rejects.toThrow('身份不匹配')
    expect(reader).not.toHaveBeenCalled()
  })
})
