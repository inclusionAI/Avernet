import { createHash, webcrypto } from 'node:crypto'
import { afterEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { originalActiveDraftBaseline, readEditorWorkflowSource } from '../editorWorkflowSource'

const doc = { id: 'flow', title: 'Active', nodes: [], content: '用户普通文本', custom: { keep: [false, 0, null] } }
const hash = (raw: string) => createHash('sha256').update(JSON.stringify(['active-workflow-baseline-v1', 'flow', 'pack', '3', '7', 'deploy/flow/#3', 'deploy', raw])).digest('hex')
const b64 = (text: string) => Buffer.from(text).toString('base64')
const pack = (row: Record<string, unknown> = doc, raw = JSON.stringify(doc)) => ({ workflowId: 'flow', packId: 'pack', baseRef: 'refs/tags/deploy/flow/#1', baseCommit: 'a'.repeat(40), draftHead: null,
  activeHistory: { protocol: 'active-history-v1', activeToken: hash(raw) }, versionFiles: [
    { path: 'workflow.pack.yaml', mode: '100644', contentBase64: b64('id: pack\nworkflows: [flow]\n') },
    { path: 'workflows/flow.yaml', mode: '100644', contentBase64: b64(stringify(row)) },
  ] })
function setup(row: Record<string, unknown> = doc) {
  vi.stubGlobal('crypto', webcrypto)
  const raw = JSON.stringify(row)
  let draft = false, active: Record<string, unknown> = { deployNumber: 3, version: 7, isActive: true, packId: 'pack', activeToken: hash(raw), tagName: 'deploy/flow/#3', action: 'deploy' }
  let response: unknown = pack(row, raw), status = 200, whenPack = () => {}
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    expect(!init?.method || init.method === 'GET').toBe(true)
    if (url === '/api/workflows/flow') return draft ? new Response(JSON.stringify(doc)) : new Response(JSON.stringify({ code: 'WORKFLOW_NOT_FOUND', workflowId: 'flow' }), { status: 404 })
    if (url.endsWith('/editor-draft')) return new Response(JSON.stringify({ workflowId: 'flow', packId: 'pack', specJson: JSON.stringify(doc), draftToken: 'd'.repeat(64) }))
    if (url.includes('/history?')) return new Response(JSON.stringify({ workflowId: 'flow', history: [], active }))
    if (url.endsWith('/by-deploy/3')) return new Response(JSON.stringify({ workflowId: 'flow', deployNumber: 3, version: 7, tagName: 'deploy/flow/#3', action: 'deploy', specJson: raw }))
    if (url.endsWith('/pack-version?version=current')) { whenPack(); return new Response(JSON.stringify(response), { status }) }
    throw new Error(`Unexpected request ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, setPack: (value: unknown, code = 200) => { response = value; status = code }, onPack: (fn: () => void) => { whenPack = fn },
    setActive: (value: Record<string, unknown>) => { active = value }, setDraft: () => { draft = true } }
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('recovers the complete direct document and binds the exact row token after verified Git read', async () => {
  const env = setup(); const result = await readEditorWorkflowSource('flow')
  expect(result).toEqual({ spec: doc, databaseSpec: null, activeDeployNumber: 3 })
  expect(originalActiveDraftBaseline(result.spec)).toMatchObject({ activeToken: hash(JSON.stringify(doc)), specJson: JSON.stringify(doc) })
  expect(env.fetchMock).toHaveBeenCalledTimes(8)
  expect(env.fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
})
it.each([401, 403, 404, 503])('rejects failed Git read HTTP %s without leaking errors', async code => {
  const env = setup(); env.setPack({ error: 'secret scripts/private.sh' }, code)
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow('无法验证生效 Git')
})
it.each([
  ['missing proof', { ...pack(), activeHistory: undefined }],
  ['wrong proof protocol', { ...pack(), activeHistory: { protocol: 'other', activeToken: hash(JSON.stringify(doc)) } }],
  ['wrong proof token', { ...pack(), activeHistory: { protocol: 'active-history-v1', activeToken: 'f'.repeat(64) } }],
  ['extra proof field', { ...pack(), activeHistory: { protocol: 'active-history-v1', activeToken: hash(JSON.stringify(doc)), other: true } }],
  ['wrong Pack', { ...pack(), packId: 'other' }],
  ['wrong workflow', { ...pack(), workflowId: 'other' }],
  ['wrong ref', { ...pack(), baseRef: 'origin/wf/flow' }],
  ['different full document', pack({ ...doc, content: 'other content' })],
  ['missing workflow', { ...pack(), versionFiles: pack().versionFiles.slice(0, 1) }],
  ['bad base64', { ...pack(), versionFiles: [pack().versionFiles[0], { ...pack().versionFiles[1], contentBase64: 'bad!' }] }],
  ['bad YAML', { ...pack(), versionFiles: [pack().versionFiles[0], { ...pack().versionFiles[1], contentBase64: b64('id: flow\ntitle: [') }] }],
  ['wrong manifest', { ...pack(), versionFiles: [{ ...pack().versionFiles[0], contentBase64: b64('id: other\nworkflows: [flow]\n') }, pack().versionFiles[1]] }],
])('rejects %s', async (_description, payload) => {
  const env = setup(); env.setPack(payload)
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow(/无法验证生效 Git|身份不匹配|不一致/)
})
it('never uses Git fallback without an active row token', async () => {
  const env = setup(); env.setActive({ deployNumber: 3, version: 7, isActive: true, packId: 'pack', tagName: 'deploy/flow/#3', action: 'deploy' })
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow('无法核验生效历史与 Git')
  expect(env.fetchMock.mock.calls.some(([url]) => url.includes('pack-version'))).toBe(false)
})
it('rejects a changed active row or newly created draft while Git is read', async () => {
  const a = setup(); a.onPack(() => a.setActive({ deployNumber: 4, version: 8, isActive: true }))
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow('生效版本已变化')
  const b = setup(); b.onPack(b.setDraft)
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow('草稿已变化')
})
it('keeps a direct document even when its content looks exactly like a valid archive projection', async () => {
  const outer = { ...doc, content: stringify(doc) }, env = setup(outer)
  const result = await readEditorWorkflowSource('flow')
  expect(result.spec).toEqual(outer)
  expect(env.fetchMock.mock.calls.some(([url]) => url.includes('pack-version'))).toBe(true)
})
it('unwraps an exact archive projection only when active Git matches its complete inner document', async () => {
  const archived = { ...doc, content: stringify(doc) }, env = setup(archived)
  env.setPack(pack(doc, JSON.stringify(archived)))
  const result = await readEditorWorkflowSource('flow')
  expect(result.spec).toEqual(doc)
  expect(originalActiveDraftBaseline(result.spec)).toMatchObject({ specJson: JSON.stringify(archived), activeToken: hash(JSON.stringify(archived)) })
})
it('never unwraps an exact projection if active Git cannot be verified', async () => {
  const archived = { ...doc, content: stringify(doc) }, env = setup(archived)
  env.setPack({ error: 'not available' }, 503)
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow('无法验证生效 Git')
})
it.each(['hello: world', '42', 'true', '', 'id: flow\ntitle: [', 'id: other\ntitle: Other\nnodes: []',
  'id: flow\ntitle: Flow\nnodes: []\nx: 9007199254740993'])('preserves direct authored text even when YAML-like or malformed: %s', async content => {
  const outer = { ...doc, content }; setup(outer)
  expect((await readEditorWorkflowSource('flow')).spec).toEqual(outer)
})
it.each(['id: other\ntitle: Other\nnodes: []', 'id: flow\ntitle: [', 'id: flow\ntitle: Flow\nnodes: []\nx: 9007199254740993'])('rejects malformed or mismatched archive when Git is not its complete outer document', async content => {
  const outer = { ...doc, content }, env = setup(outer)
  env.setPack(pack(doc, JSON.stringify(outer)))
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow()
})
it('rejects even a valid inner workflow if the outer projection has extra fields', async () => {
  const outer = { ...doc, content: stringify(doc), extra: 'do not discard' }, env = setup(outer)
  env.setPack(pack(doc, JSON.stringify(outer)))
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow('不一致')
})
