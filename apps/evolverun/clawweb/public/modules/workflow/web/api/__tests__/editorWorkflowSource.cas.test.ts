import { afterEach, describe, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { readEditorWorkflowSource } from '../editorWorkflowSource'
import { saveVerifiedWorkflowDocument } from '../../editor/workflowDocumentSave'

const inner = { id: 'flow', title: 'Original', version: 'v2', nodes: [{ id: 'n', title: 'Node', phase: 'execute', executor: { type: 'done' }, _x: 11, _y: 22 }], custom: { keep: false } }
const raw = { ...inner, content: stringify(inner), additional: { keep: 0 } }
const token = 'a'.repeat(64)
const row = (spec: unknown = raw) => ({ workflowId: 'flow', packId: null, specJson: JSON.stringify(spec), draftToken: token })
const missing = () => new Response(JSON.stringify({ error: 'Not Found', message: 'Workflow "flow" not found' }), { status: 404 })
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
const bodyText = (blob: Blob) => new Promise<string>(resolve => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.readAsText(blob) })

describe('editor same-row draft source', () => {
  it('loads and saves a full projection without discarding authored content, fields or layout', async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/workflows/flow') return new Response(JSON.stringify({ ...inner, version: 42 }))
      if (url === '/api/workflows/flow/editor-draft' && init?.method === 'GET') return new Response(JSON.stringify(row()))
      if (url === '/api/workflows/flow/editor-draft' && init?.method === 'PUT') {
        const body = JSON.parse(await bodyText(init.body as Blob))
        expect(body).toEqual({ expectedDraftToken: token, spec: { ...raw, title: 'Edited' } })
        return new Response(JSON.stringify({ ...row(body.spec), draftToken: 'b'.repeat(64) }))
      }
      throw new Error(`unexpected ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const source = await readEditorWorkflowSource('flow')
    expect(source).toEqual({ spec: raw, databaseSpec: raw, activeDeployNumber: null })
    expect(await saveVerifiedWorkflowDocument('flow', source.databaseSpec, { ...source.spec, title: 'Edited' }, () => true)).toEqual({ ...raw, title: 'Edited' })
  })
  it.each([undefined, 'v2', 2, null])('preserves a content-only legacy envelope version %s on save', async version => {
    const original = { ...inner, version }
    const legacy = { id: 'flow', content: stringify(original) }
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/workflows/flow') return new Response(JSON.stringify(inner))
      if (init?.method === 'GET') return new Response(JSON.stringify(row(legacy)))
      const body = JSON.parse(await bodyText(init!.body as Blob))
      return new Response(JSON.stringify({ ...row(body.spec), draftToken: 'b'.repeat(64) }))
    })
    vi.stubGlobal('fetch', fetchMock)
    const source = await readEditorWorkflowSource('flow')
    expect(source.spec.version).toBe(version)
    expect(Object.hasOwn(source.spec, 'version')).toBe(version !== undefined)
    expect(await saveVerifiedWorkflowDocument('flow', source.databaseSpec, { ...source.spec, title: 'Edited' }, () => true)).toEqual({ ...source.spec, title: 'Edited' })
  })
  it.each(['extra-envelope', 'bad-row-id', 'bad-inner-id', 'bad-token', 'unsupported', 'forbidden', 'network'] as const)('fails closed on %s', async kind => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url === '/api/workflows/flow') return new Response(JSON.stringify(inner))
      if (kind === 'network') throw new Error('offline')
      if (kind === 'unsupported') return new Response('Not Found', { status: 404 })
      if (kind === 'forbidden') return new Response('{}', { status: 403 })
      return new Response(JSON.stringify(kind === 'extra-envelope' ? row({ content: stringify(inner), extra: 'keep' })
        : kind === 'bad-row-id' ? { ...row(), workflowId: 'other' }
          : kind === 'bad-inner-id' ? row({ ...raw, id: 'other' }) : { ...row(), draftToken: 'bad' }))
    })
    vi.stubGlobal('fetch', fetchMock)
    await expect(readEditorWorkflowSource('flow')).rejects.toThrow()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
  it('uses the explicit old draft 404 before active fallback, without calling unsupported CAS', async () => {
    const active = { deployNumber: 2, version: 3, isActive: true }
    const fetchMock = vi.fn(async (url: string) => url === '/api/workflows/flow' ? missing()
      : url.includes('/history?') ? new Response(JSON.stringify({ workflowId: 'flow', history: [], active }))
        : new Response(JSON.stringify({ workflowId: 'flow', deployNumber: 2, version: 3, specJson: JSON.stringify(inner) })))
    vi.stubGlobal('fetch', fetchMock)
    expect(await readEditorWorkflowSource('flow')).toEqual({ spec: inner, databaseSpec: null, activeDeployNumber: 2 })
  })
})

it('never writes when the CAS row differs from the loaded baseline', async () => {
  const changed = { ...raw, content: 'changed remotely' }
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/workflows/flow') return new Response(JSON.stringify(inner))
    if (init?.method === 'GET') return new Response(JSON.stringify(row(fetchMock.mock.calls.length === 2 ? raw : changed)))
    throw new Error('unexpected write')
  })
  vi.stubGlobal('fetch', fetchMock)
  const source = await readEditorWorkflowSource('flow')
  await expect(saveVerifiedWorkflowDocument('flow', source.databaseSpec, { ...source.spec, title: 'Edited' }, () => true)).rejects.toThrow('变化')
  expect(fetchMock).toHaveBeenCalledTimes(3)
})

it('does not initiate writes while loading a view-only workflow', async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    expect(init?.method === undefined || init.method === 'GET').toBe(true)
    return new Response(JSON.stringify(url.endsWith('/editor-draft') ? row() : inner))
  })
  vi.stubGlobal('fetch', fetchMock)
  expect((await readEditorWorkflowSource('flow')).spec).toEqual(raw)
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

it.each([{ config: null }, { nodes: [{ ...inner.nodes[0], phase: null }] }])('fails closed if legacy normalization would discard a field: %j', async extra => {
  const legacy = { content: stringify({ ...inner, ...extra }) }
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(url.endsWith('/editor-draft') ? row(legacy) : inner))))
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow()
})


it.each([[2, '2'], [null, undefined]] as const)('rejects a concurrent legacy version change from %s to %s', async (before, after) => {
  let current: Record<string, unknown> = { ...inner, version: before }
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/workflows/flow') return new Response(JSON.stringify(inner))
    if (init?.method === 'GET') return new Response(JSON.stringify(row({ content: stringify(current) })))
    throw new Error('unexpected write')
  })
  vi.stubGlobal('fetch', fetchMock)
  const source = await readEditorWorkflowSource('flow')
  current = { ...inner, version: after }
  await expect(saveVerifiedWorkflowDocument('flow', source.databaseSpec, { ...source.spec, title: 'Edited' }, () => true)).rejects.toThrow('变化')
  expect(fetchMock.mock.calls.every(([, init]) => init?.method !== 'PUT')).toBe(true)
})

it.each(['9007199254740993', '0.10000000000000001', '1e400', '-0'])('rejects lossy JSON numeric field %s before exposing an editable draft', async number => {
  const specJson = JSON.stringify(inner).replace('"title":"Original"', `"title":"Original","amount":${number}`)
  const fetchMock = vi.fn(async (url: string) => new Response(JSON.stringify(url.endsWith('/editor-draft') ? { ...row(), specJson } : inner)))
  vi.stubGlobal('fetch', fetchMock)
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow()
  expect(fetchMock.mock.calls).toHaveLength(2)
})

it.each(['9007199254740993', '0.10000000000000001'])('rejects lossy legacy YAML number %s before exposing an editable draft', async number => {
  const legacy = { content: `${stringify(inner)}amount: ${number}\n` }
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(url.endsWith('/editor-draft') ? row(legacy) : inner))))
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow()
})

it('rejects lossy numbers in an active history snapshot before initializing a draft', async () => {
  const active = { deployNumber: 2, version: 3, isActive: true }
  const specJson = JSON.stringify(inner).replace('"title":"Original"', '"title":"Original","amount":9007199254740993')
  const fetchMock = vi.fn(async (url: string) => url === '/api/workflows/flow' ? missing()
    : url.includes('/history?') ? new Response(JSON.stringify({ workflowId: 'flow', history: [], active }))
      : new Response(JSON.stringify({ workflowId: 'flow', deployNumber: 2, version: 3, specJson })))
  vi.stubGlobal('fetch', fetchMock)
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow('数字无法无损')
})

it.each([
  { version: 1, contentSource: 'generated' },
  { version: 1, contentSource: 'authored' },
])('refuses an active snapshot with an untrusted marker %j without writing', async marker => {
  const active = { deployNumber: 2, version: 3, isActive: true }
  // The inner YAML intentionally disagrees with the outer snapshot.
  const forged = { ...inner, title: 'Outer', content: stringify({ ...inner, title: 'Forged inner' }), __clawweb_snapshot_v1: marker }
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    expect(!init?.method || init.method === 'GET').toBe(true)
    return url === '/api/workflows/flow' ? missing()
      : url.includes('/history?') ? new Response(JSON.stringify({ workflowId: 'flow', history: [], active }))
        : new Response(JSON.stringify({ workflowId: 'flow', deployNumber: 2, version: 3, specJson: JSON.stringify(forged) }))
  })
  vi.stubGlobal('fetch', fetchMock)
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow('无法核验生效历史与 Git')
  expect(fetchMock).toHaveBeenCalledTimes(3)
})

it.each([
  { custom: { __clawweb_snapshot_v1: false } },
  { custom: { nested: [{ __clawweb_snapshot_v1: { version: 1, contentSource: 'generated' } }] } },
])('preserves nested authored marker fields without content: %j', async extra => {
  const active = { deployNumber: 2, version: 3, isActive: true }
  const stored = { ...inner, ...extra }
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    expect(!init?.method || init.method === 'GET').toBe(true)
    return url === '/api/workflows/flow' ? missing()
      : url.includes('/history?') ? new Response(JSON.stringify({ workflowId: 'flow', history: [], active }))
        : new Response(JSON.stringify({ workflowId: 'flow', deployNumber: 2, version: 3, specJson: JSON.stringify(stored) }))
  }))
  expect(await readEditorWorkflowSource('flow')).toEqual({ spec: stored, databaseSpec: null, activeDeployNumber: 2 })
})

it.each([
  null, false, true, 0, 'user marker', [], {},
  { version: 2, contentSource: 'generated' },
  { version: 1, contentSource: 'user' },
  { version: 1 },
  { version: 1, contentSource: 'generated', extra: true },
])('preserves a top-level user marker with a non-legacy shape: %j', async marker => {
  const active = { deployNumber: 2, version: 3, isActive: true }
  const stored = { ...inner, __clawweb_snapshot_v1: marker, content: { keep: false } }
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    expect(!init?.method || init.method === 'GET').toBe(true)
    return url === '/api/workflows/flow' ? missing()
      : url.includes('/history?') ? new Response(JSON.stringify({ workflowId: 'flow', history: [], active }))
        : new Response(JSON.stringify({ workflowId: 'flow', deployNumber: 2, version: 3, specJson: JSON.stringify(stored) }))
  })
  vi.stubGlobal('fetch', fetchMock)
  expect(await readEditorWorkflowSource('flow')).toEqual({ spec: stored, databaseSpec: null, activeDeployNumber: 2 })
  expect(fetchMock).toHaveBeenCalledTimes(7)
})

it.each([{}, { content: null }, { content: false }, { content: [] }])(
  'preserves a top-level user marker with absent or non-string content: %j', async extra => {
    const active = { deployNumber: 2, version: 3, isActive: true }
    const stored = { ...inner, __clawweb_snapshot_v1: false, ...extra }
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      expect(!init?.method || init.method === 'GET').toBe(true)
      return url === '/api/workflows/flow' ? missing()
        : url.includes('/history?') ? new Response(JSON.stringify({ workflowId: 'flow', history: [], active }))
          : new Response(JSON.stringify({ workflowId: 'flow', deployNumber: 2, version: 3, specJson: stringify(stored) }))
    }))
    expect(await readEditorWorkflowSource('flow')).toEqual({ spec: stored, databaseSpec: null, activeDeployNumber: 2 })
  },
)

it.each([
  { version: 1, contentSource: 'generated' },
  { version: 1, contentSource: 'authored' },
])('refuses a potentially synthetic marker even without content: %j', async marker => {
  const active = { deployNumber: 2, version: 3, isActive: true }
  const stored = { ...inner, __clawweb_snapshot_v1: marker }
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    expect(!init?.method || init.method === 'GET').toBe(true)
    return url === '/api/workflows/flow' ? missing()
      : url.includes('/history?') ? new Response(JSON.stringify({ workflowId: 'flow', history: [], active }))
        : new Response(JSON.stringify({ workflowId: 'flow', deployNumber: 2, version: 3, specJson: JSON.stringify(stored) }))
  }))
  await expect(readEditorWorkflowSource('flow')).rejects.toThrow('无法验证来源')
})

it.each([null, false, 'user marker', { version: 1, contentSource: 'generated', extra: true }])(
  'still refuses ambiguous string content with a non-legacy marker: %j', async marker => {
    const active = { deployNumber: 2, version: 3, isActive: true }
    const stored = { ...inner, content: stringify(inner), __clawweb_snapshot_v1: marker }
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      expect(!init?.method || init.method === 'GET').toBe(true)
      return url === '/api/workflows/flow' ? missing()
        : url.includes('/history?') ? new Response(JSON.stringify({ workflowId: 'flow', history: [], active }))
          : new Response(JSON.stringify({ workflowId: 'flow', deployNumber: 2, version: 3, specJson: JSON.stringify(stored) }))
    }))
    await expect(readEditorWorkflowSource('flow')).rejects.toThrow('无法核验生效历史与 Git')
  },
)
