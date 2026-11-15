import { createHash, webcrypto } from 'node:crypto'
import { afterEach, expect, it, vi } from 'vitest'
import { candidateProjection } from '../../../server/services/pack-operation-service'
import { originalActiveDraftBaseline, readEditorWorkflowSource } from '../editorWorkflowSource'
import { historyPackFixture } from './historyPackFixture'

const yaml = `id: !!str flow
title: !!str Tagged workflow
version: !!str 7
nodes: !!seq
  - id: start
    title: Start
    executor: {type: start}
    dependsOn: []
  - id: end
    title: End
    executor: {type: end}
    dependsOn: [start]
content: !!str "authored content"
custom: !!map
  string: !!str 012
  number: !!int 42
  fraction: !!float 1.5
  enabled: !!bool true
  empty: !!null null
`
const expected = { id: 'flow', title: 'Tagged workflow', version: '7', nodes: [
  { id: 'start', title: 'Start', executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', title: 'End', executor: { type: 'end' }, dependsOn: ['start'] },
], content: 'authored content', custom: { string: '012', number: 42, fraction: 1.5, enabled: true, empty: null } }
const archive = () => candidateProjection('flow', yaml, '{}', 'legacy-autoheal').specJson
const hash = (specJson: string) => createHash('sha256').update(JSON.stringify([
  'active-workflow-baseline-v1', 'flow', 'pack', '3', '7', 'deploy/flow/#3', 'deploy', specJson,
])).digest('hex')

function setup(specJson = archive(), legacy = false, gitYaml = yaml) {
  vi.stubGlobal('crypto', webcrypto)
  let active: Record<string, unknown> = { deployNumber: 3, version: 7, isActive: true, packId: 'pack',
    tagName: 'deploy/flow/#3', action: 'deploy', ...(legacy ? {} : { activeToken: hash(specJson) }) }
  let row = specJson
  let draft = false
  let historyReads = 0, snapshotReads = 0, draftReads = 0
  let onHistory: ((n: number) => void) | undefined
  let onSnapshot: ((n: number) => void) | undefined
  let onDraft: ((n: number) => void) | undefined
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    expect(!init?.method || init.method === 'GET').toBe(true)
    if (url === '/api/workflows/flow') {
      onDraft?.(++draftReads)
      return draft ? new Response(JSON.stringify(expected))
        : new Response(JSON.stringify({ code: 'WORKFLOW_NOT_FOUND', workflowId: 'flow' }), { status: 404 })
    }
    if (url === '/api/workflows/flow/editor-draft') return new Response(JSON.stringify({ workflowId: 'flow', packId: 'pack', specJson: JSON.stringify(expected), draftToken: 'a'.repeat(64) }))
    if (url.endsWith('/pack-version?version=current')) return new Response(JSON.stringify(historyPackFixture(hash(specJson), gitYaml)))
    if (url.includes('/history?')) { onHistory?.(++historyReads); return new Response(JSON.stringify({ workflowId: 'flow', history: [], active })) }
    if (url.endsWith('/by-deploy/3')) { onSnapshot?.(++snapshotReads); return new Response(JSON.stringify({ workflowId: 'flow', deployNumber: 3, version: 7, tagName: 'deploy/flow/#3', action: 'deploy', specJson: row })) }
    throw new Error(`Unexpected request ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, setDraft: () => { draft = true }, setActive: (value: Record<string, unknown>) => { active = value },
    setRow: (value: string) => { row = value }, onHistory: (fn: (n: number) => void) => { onHistory = fn },
    onSnapshot: (fn: (n: number) => void) => { onSnapshot = fn }, onDraft: (fn: (n: number) => void) => { onDraft = fn } }
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('recovers JSON-compatible explicit tags from the real legacy writer projection', async () => {
  const specJson = archive()
  const env = setup(specJson)
  const result = await readEditorWorkflowSource('flow')
  expect(result.spec).toEqual(expected)
  expect(originalActiveDraftBaseline(result.spec)).toMatchObject({ specJson, activeToken: hash(specJson) })
  expect(JSON.parse(specJson).content).toBe(yaml)
  expect(env.fetchMock).toHaveBeenCalledTimes(8)
})

it.each([
  ['malformed YAML', 'id: flow\ntitle: [', /格式无效/],
  ['unsafe integer', 'id: flow\ntitle: Flow\nnodes: []\nx: 9007199254740993', /数字无法无损/],
  ['lossy decimal', 'id: flow\ntitle: Flow\nnodes: []\nx: 0.10000000000000001', /数字无法无损/],
  ['duplicate key', 'id: flow\nid: flow\ntitle: Flow\nnodes: []', /格式无效/],
  ['unsupported set tag', 'id: flow\ntitle: Flow\nnodes: []\nx: !!set {a: null}', /YAML 标签/],
  ['unknown tag', 'id: flow\ntitle: Flow\nnodes: []\nx: !custom value', /格式无效|YAML 标签/],
])('rejects %s without invoking the trusted reader', async (_case, text, message) => {
  const specJson = JSON.stringify({ ...expected, content: text })
  setup(specJson)
  const reader = vi.fn(async () => null)
  await expect(readEditorWorkflowSource('flow', { readTrustedActiveHistory: reader })).rejects.toThrow(message)
  expect(reader).not.toHaveBeenCalled()
})

it('rejects a differing projection without consulting the trusted reader', async () => {
  const specJson = JSON.stringify({ ...expected, title: 'Different', content: yaml })
  setup(specJson)
  const reader = vi.fn(async () => null)
  await expect(readEditorWorkflowSource('flow', { readTrustedActiveHistory: reader })).rejects.toThrow('不一致')
  expect(reader).not.toHaveBeenCalled()
})

it('rejects an active deployment change during normal recovery', async () => {
  const env = setup()
  env.onHistory(n => { if (n === 2) env.setActive({ deployNumber: 4, version: 8, isActive: true }) })
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow('生效版本已变化')
})
it('rejects a draft created before the first recovery recheck', async () => {
  const env = setup()
  env.onDraft(n => { if (n === 2) env.setDraft() })
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow('草稿已变化')
})
it('rejects a draft created during the snapshot recheck', async () => {
  const env = setup()
  env.onSnapshot(n => { if (n === 2) env.setDraft() })
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow('草稿已变化')
})
it.each([true, false])('rejects changed snapshot bytes even for old servers (legacy=%s)', async legacy => {
  const env = setup(archive(), legacy)
  env.onSnapshot(n => { if (n === 2) env.setRow(JSON.stringify({ ...expected, title: 'Changed' })) })
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow(legacy ? '无法核验生效历史与 Git' : '生效快照已变化')
})
it('rejects ambiguous legacy snapshots without a token', async () => {
  setup(archive(), true)
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow('无法核验生效历史与 Git')
})

it('does not invoke the trusted reader for a matching but invalid inner workflow', async () => {
  const invalid = 'id: flow\ntitle: Flow\nnodes: [{id: broken}]\n'
  setup(JSON.stringify({ id: 'flow', title: 'Flow', nodes: [{ id: 'broken' }], content: invalid }), false, invalid)
  const reader = vi.fn(async () => null)
  await expect(readEditorWorkflowSource('flow', { readTrustedActiveHistory: reader })).rejects.toThrow('nodes[0]')
  expect(reader).not.toHaveBeenCalled()
})


it.each(['other', '', null, 42])('rejects inner workflow identity %s before consulting a trusted reader', async innerId => {
  const inner = { ...expected, id: innerId }
  const specJson = JSON.stringify({ ...expected, content: JSON.stringify(inner) })
  const env = setup(specJson)
  const reader = vi.fn(async () => null)
  await expect(readEditorWorkflowSource('flow', { readTrustedActiveHistory: reader })).rejects.toThrow('身份不匹配')
  expect(reader).not.toHaveBeenCalled()
  expect(env.fetchMock).toHaveBeenCalledTimes(4)
})

it.each([
  ['numeric keys', 'extra: {1: one, 2: two}\n'],
  ['boolean keys', 'extra: {true: yes, false: no}\n'],
  ['alias key', 'key_name: &key label\nextra:\n  ? *key\n  : value\n'],
])('fails closed for legacy-writer %s pending safe key conversion support', async (_kind, extra) => {
  const tagged = yaml + extra
  const specJson = candidateProjection('flow', tagged, '{}', 'legacy-autoheal').specJson
  setup(specJson)
  const reader = vi.fn(async () => null)
  await expect(readEditorWorkflowSource('flow', { readTrustedActiveHistory: reader })).rejects.toThrow('映射键必须是字符串')
  expect(reader).not.toHaveBeenCalled()
})

it('rejects a missing inner workflow identity before consulting a trusted reader', async () => {
  const { id: _id, ...inner } = expected
  const env = setup(JSON.stringify({ ...expected, content: JSON.stringify(inner) }))
  const reader = vi.fn(async () => null)
  await expect(readEditorWorkflowSource('flow', { readTrustedActiveHistory: reader })).rejects.toThrow('身份不匹配')
  expect(reader).not.toHaveBeenCalled()
  expect(env.fetchMock).toHaveBeenCalledTimes(4)
})
