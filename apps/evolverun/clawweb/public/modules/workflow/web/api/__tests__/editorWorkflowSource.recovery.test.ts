import { createHash, webcrypto } from 'node:crypto'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { originalActiveDraftBaseline, readEditorWorkflowSource } from '../editorWorkflowSource'

const document = { id: 'flow', title: 'Active', version: null, nodes: [], custom: { nested: [false, 0, null], content: 'keep' } }
const active = { deployNumber: 3, version: 7, isActive: true }

function installSnapshot(specJson: string, activeRecord: Record<string, unknown> = active) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    expect(!init?.method || init.method === 'GET').toBe(true)
    if (url === '/api/workflows/flow') return new Response(JSON.stringify({ code: 'WORKFLOW_NOT_FOUND', workflowId: 'flow' }), { status: 404 })
    if (url.includes('/history?')) return new Response(JSON.stringify({ workflowId: 'flow', history: [], active: activeRecord }))
    if (url.endsWith('/by-deploy/3')) return new Response(JSON.stringify({ workflowId: 'flow', deployNumber: 3, version: 7, tagName: activeRecord.tagName, action: activeRecord.action, specJson }))
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('whole-document historical string recovery', () => {
  it.each(['yaml', 'json'])('recovers a JSON-encoded %s document without dropping fields or writing', async format => {
    const sourceText = format === 'yaml' ? stringify(document) : JSON.stringify(document)
    const fetchMock = installSnapshot(JSON.stringify(sourceText))
    expect(await readEditorWorkflowSource('flow')).toEqual({ spec: document, databaseSpec: null, activeDeployNumber: 3 })
    expect(fetchMock).toHaveBeenCalledTimes(7)
  })


  it('binds the exact raw historical string to a verified active token, not the decoded bytes', async () => {
    const specJson = JSON.stringify(stringify(document))
    const activeToken = createHash('sha256').update(JSON.stringify(['active-workflow-baseline-v1', 'flow', 'pack', '3', '7', 'v7', 'deploy', specJson])).digest('hex')
    vi.stubGlobal('crypto', webcrypto)
    installSnapshot(specJson, { ...active, packId: 'pack', activeToken, tagName: 'v7', action: 'deploy' })
    const source = await readEditorWorkflowSource('flow')
    expect(source.spec).toEqual(document)
    expect(originalActiveDraftBaseline(source.spec)).toMatchObject({ workflowId: 'flow', packId: 'pack', activeToken, specJson })
  })

  it.each(['generated', 'authored'])('does not let a valid row hash and extra %s marker authorize stripping content', async contentSource => {
    const archived = { ...document, content: stringify({ ...document, content: 'user payload' }),
      ...(contentSource === 'absent' ? {} : { __clawweb_snapshot_v1: { version: 1, contentSource } }) }
    const specJson = JSON.stringify(archived)
    const activeToken = createHash('sha256').update(JSON.stringify(['active-workflow-baseline-v1', 'flow', 'pack', '3', '7', 'v7', 'deploy', specJson])).digest('hex')
    vi.stubGlobal('crypto', webcrypto)
    const fetchMock = installSnapshot(specJson, { ...active, packId: 'pack', activeToken, tagName: 'v7', action: 'deploy' })
    await expect(readEditorWorkflowSource('flow')).rejects.toThrow('无法验证生效 Git')
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it.each([
    JSON.stringify({ ...document, content: 'authored' }),
    JSON.stringify({ ...document, content: stringify(document), __clawweb_snapshot_v1: { version: 1, contentSource: 'generated' } }),
    JSON.stringify({ id: 'flow', content: stringify(document) }),
    JSON.stringify(JSON.stringify(document)),
  ])('does not bypass the normal ambiguity or structure checks inside a string: %s', async inner => {
    const fetchMock = installSnapshot(JSON.stringify(inner))
    await expect(readEditorWorkflowSource('flow')).rejects.toThrow(/无法核验生效历史与 Git|生效快照/)
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it.each([
    stringify({ ...document, id: 'other' }),
    `${stringify(document)}unsafe: 9007199254740993\n`,
    `${stringify(document)}custom: duplicate\n`,
    'nodes: [',
    `${stringify(document)}extra: !!set {a: null}\n`,
    `${stringify(document)}extra: !custom value\n`,
  ])('rejects invalid, identity-mismatched or lossy inner data: %s', async inner => {
    const fetchMock = installSnapshot(JSON.stringify(inner))
    await expect(readEditorWorkflowSource('flow')).rejects.toThrow()
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })
})
