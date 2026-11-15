import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createHash, webcrypto } from 'node:crypto'
import { parse, stringify } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const transport = vi.hoisted(() => ({ fetchJson: vi.fn() }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => transport)
import { generateWorkflowAiDraft, workflowSpecSignature } from './workflow-ai-draft'
import { workflowAiYaml } from './workflow-ai-spec'
import { WorkflowAiRecoveryError } from './workflow-ai-lifecycle'

const wire = vi.fn()
const capabilities = vi.fn()
let inputYaml: string
function submitted() { return { draftId: 'x', status: 'pending' } }
function task(value: object) { return { draftId: 'x', workflowId: source.id, inputSpecDigest: createHash('sha256').update(inputYaml).digest('hex'), ...value } }

const source: WorkflowSpec = {
  id: 'perception-ticket-realtime', version: '26', title: '感知工单实时分析与处置',
  config: { preserve: true },
  nodes: [
    { id: 'analysis', title: '分析', executor: { type: 'embedded-agent', prompt: 'original' }, _x: 12, _y: 24 },
    { id: 'hitl-confirm', title: '已有确认', executor: { type: 'human', message: '保留真实配置' }, dependsOn: ['analysis'] },
    { id: 'done', title: '完成', executor: { type: 'done' }, dependsOn: ['hitl-confirm'] },
  ],
}
const candidate = (): WorkflowSpec => ({
  ...structuredClone(source),
  nodes: source.nodes.map(n => n.id === 'analysis' ? { ...n, executor: { ...n.executor, prompt: 'new prompt' } } : structuredClone(n)),
})
function success(spec: WorkflowSpec = candidate(), baseline: WorkflowSpec = source) {
  inputYaml = workflowAiYaml(baseline)
  wire.mockResolvedValueOnce(submitted())
    .mockResolvedValueOnce(task({ status: 'completed', result: { yaml: stringify(spec), summary: '修改分析提示词' } }))
    .mockImplementationOnce(async (_url, init) => ({ valid: true, issues: [], normalizedSpec: JSON.parse(init.body).spec }))
}

beforeEach(() => {
  vi.clearAllMocks(); wire.mockReset(); capabilities.mockReset(); capabilities.mockResolvedValue({ available: true })
  inputYaml = workflowAiYaml(source)
  vi.stubGlobal('crypto', { randomUUID: () => webcrypto.randomUUID(), subtle: { digest: async (_algorithm, bytes) => createHash('sha256').update(bytes).digest().buffer } })
  transport.fetchJson.mockImplementation((url, init) => {
    if (url.endsWith('/capabilities')) return capabilities(url, init)
    if (url.endsWith('/cancel')) return Promise.resolve(task({ status: 'cancelled' }))
    if (init?.method === 'POST' && !url.endsWith('/validate')) inputYaml = JSON.parse(init.body).currentYaml
    return wire(url, init)
  })
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('real workflow AI draft generation', () => {
  it('submits the actual prompt and current YAML, polls, validates, and does not mutate the editor or save', async () => {
    const before = structuredClone(source)
    success()
    const draft = await generateWorkflowAiDraft('modify', '只修改分析提示词', source)
    const [url, init] = wire.mock.calls[0]
    expect(url).toBe(`/api/workflows/${source.id}/ai-drafts`)
    const body = JSON.parse(init.body)
    expect(body.prompt).toBe('只修改分析提示词')
    expect(body.mode).toBe('modify')
    expect(body.requestId).toMatch(/^[0-9a-f-]{36}$/)
    expect(capabilities).toHaveBeenCalledOnce()
    expect(body.currentYaml).toContain('保留真实配置')
    expect(wire.mock.calls.map(call => call[0])).toEqual([
      `/api/workflows/${source.id}/ai-drafts`, `/api/workflows/${source.id}/ai-drafts/x`, '/api/workflows/validate',
    ])
    expect(draft.spec.nodes[0].executor.prompt).toBe('new prompt')
    expect(draft.spec.nodes[0]._x).toBe(12)
    expect(draft.spec.nodes[1]).toEqual(source.nodes[1])
    expect(draft.changes).toEqual([expect.objectContaining({ kind: 'update', nodeId: 'analysis' })])
    expect(draft.removedNodeIds).toEqual([])
    expect(draft.metadataChanges).toEqual([])
    expect(source).toEqual(before)
  })

  it.each([
    ['if/then/else', {
      if: { approved: true },
      then: { wait: { type: 'human', prompt: '确认分析结果', saveAs: { approved: '{{input.approved}}' } } },
      else: { rerun: { target: 'analysis', reset: 'target-and-descendants', feedbackTemplate: '请重新分析' } },
      extension: { preserve: ['opaque', { enabled: true }] },
    }],
    ['branches', {
      branches: [{ branchId: 'accepted', match: { status: 'accepted' }, complete: true }],
      default: { complete: false },
      extension: { preserve: ['opaque', { enabled: true }] },
    }],
  ])('preserves an existing opaque %s onResult when an unrelated prompt changes', async (_form, onResult) => {
    const existing = structuredClone(source)
    // The shared editor type still only describes legacy target arrays, not the runtime object protocol.
    Object.assign(existing.nodes[1], { onResult })
    const before = structuredClone(existing)
    const next = structuredClone(existing)
    next.nodes[0].executor.prompt = 'new prompt'
    success(next, existing)

    const draft = await generateWorkflowAiDraft('modify', '只修改分析提示词', existing)

    const contextYaml = JSON.parse(wire.mock.calls[0][1].body).currentYaml
    const validatedSpec = JSON.parse(wire.mock.calls[2][1].body).spec
    expect(parse(contextYaml).nodes[1].onResult).toStrictEqual(onResult)
    expect(validatedSpec.nodes[1].onResult).toStrictEqual(onResult)
    expect(draft.spec.nodes[1]).toStrictEqual(existing.nodes[1])
    expect(parse(draft.yaml).nodes[1].onResult).toStrictEqual(onResult)
    expect(draft.spec.nodes[0].executor.prompt).toBe('new prompt')
    expect(draft.changes).toEqual([expect.objectContaining({ kind: 'update', nodeId: 'analysis' })])
    expect(existing).toStrictEqual(before)
  })

  it('preserves known array-form result branches targeting existing nodes', async () => {
    const next = candidate()
    next.nodes[0].onResult = [{ value: 'confirmed', target: 'hitl-confirm' }]
    success(next)
    const draft = await generateWorkflowAiDraft('modify', '修改', source)
    expect(draft.spec.nodes[0].onResult).toEqual(next.nodes[0].onResult)
  })

  it('waits for a pending generation rather than returning mock nodes', async () => {
    vi.useFakeTimers()
    wire.mockResolvedValueOnce(submitted())
      .mockResolvedValueOnce(task({ status: 'running' }))
      .mockResolvedValueOnce(task({ status: 'completed', result: { yaml: stringify(candidate()), summary: 'new' } }))
      .mockImplementationOnce(async (_url, init) => ({ valid: true, issues: [], normalizedSpec: JSON.parse(init.body).spec }))
    const promise = generateWorkflowAiDraft('create', '增加能力', source)
    await vi.advanceTimersByTimeAsync(3000)
    expect((await promise).spec.nodes).toHaveLength(3)
  })

  it('rejects blank prompts before any HTTP call', async () => {
    await expect(generateWorkflowAiDraft('create', ' ', source)).rejects.toThrow('请输入')
    expect(wire).not.toHaveBeenCalled()
  })

  it.each([
    ['null', '结构'],
    ['id: other\nversion: 26\ntitle: other\nnodes: []', 'ID'],
    [stringify({ ...candidate(), version: '27' }), '版本'],
    ['id: [broken', 'YAML'],
    [stringify({ ...candidate(), nodes: [...source.nodes, source.nodes[0]] }), '重复'],
    [stringify({ ...candidate(), nodes: [{ ...source.nodes[0], dependsOn: ['missing'] }] }), '依赖'],
    [stringify({ ...candidate(), nodes: [{ ...source.nodes[0], dependsOn: ['analysis'] }] }), '环'],
  ])('rejects unsafe or invalid model YAML: %s', async (yaml, error) => {
    wire.mockResolvedValueOnce(submitted())
      .mockResolvedValueOnce(task({ status: 'completed', result: { yaml, summary: 'bad' } }))
    await expect(generateWorkflowAiDraft('modify', '修改', source)).rejects.toThrow(error)
  })

  it.each([
    [{ value: 'confirmed', target: 'missing' }],
    [{ value: 'confirmed' }],
    [null],
    [{ value: 'confirmed', target: 1 }],
  ])('rejects malformed or missing known array targets before backend validation: %j', async branch => {
    const next = candidate()
    next.nodes.push({ id: '1', title: 'Numeric-looking ID', executor: { type: 'done' } })
    Object.assign(next.nodes[0], { onResult: [branch] })
    wire.mockResolvedValueOnce(submitted())
      .mockResolvedValueOnce(task({ status: 'completed', result: { yaml: stringify(next) } }))
    await expect(generateWorkflowAiDraft('modify', '修改', source)).rejects.toThrow('结果分支')
    expect(wire).toHaveBeenCalledTimes(2)
  })

  it('reports deletions and top-level changes rather than silently preserving a lossy replacement', async () => {
    const next = candidate()
    next.config = { preserve: false }
    next.nodes = [next.nodes[0]]
    success(next)
    const draft = await generateWorkflowAiDraft('modify', '移除后续节点', source)
    expect(draft.removedNodeIds).toEqual(['hitl-confirm', 'done'])
    expect(draft.metadataChanges).toContain('config')
    expect(draft.changes.filter(change => change.kind === 'remove')).toHaveLength(2)
  })

  it('blocks backend validation failures, and never applies an unvalidated candidate', async () => {
    wire.mockResolvedValueOnce(submitted())
      .mockResolvedValueOnce(task({ status: 'completed', result: { yaml: stringify(candidate()), summary: 'generated' } }))
      .mockResolvedValueOnce({ valid: false, issues: [{ path: 'nodes', message: 'server says invalid', severity: 'error' }], normalizedSpec: null })
    await expect(generateWorkflowAiDraft('modify', '修改', source)).rejects.toThrow('server says invalid')
  })

  it('delegates object-form result-branch protocol validation to the backend', async () => {
    const next = candidate()
    const onResult = { if: { approved: true }, then: { wait: { type: 'unsupported', prompt: '确认' } } }
    Object.assign(next.nodes[1], { onResult })
    wire.mockResolvedValueOnce(submitted())
      .mockResolvedValueOnce(task({ status: 'completed', result: { yaml: stringify(next) } }))
      .mockResolvedValueOnce({
        valid: false, normalizedSpec: null,
        issues: [{ path: 'nodes[hitl-confirm].onResult.then.wait.type', message: 'must be human', severity: 'error' }],
      })

    await expect(generateWorkflowAiDraft('modify', '修改', source)).rejects.toThrow('must be human')

    expect(wire).toHaveBeenCalledTimes(3)
    expect(wire.mock.calls[2][0]).toBe('/api/workflows/validate')
    expect(JSON.parse(wire.mock.calls[2][1].body).spec.nodes[1].onResult).toStrictEqual(onResult)
  })

  it.each([
    ['id', 'another-workflow', 'ID'],
    ['version', '27', '版本'],
    ['id', undefined, 'ID'],
    ['version', undefined, '版本'],
  ])('revalidates normalizedSpec %s=%s even when the generated YAML preserves identity', async (field, value, error) => {
    const before = structuredClone(source)
    wire.mockResolvedValueOnce(submitted())
      .mockResolvedValueOnce(task({ status: 'completed', result: { yaml: stringify(candidate()) } }))
      .mockResolvedValueOnce({ valid: true, issues: [], normalizedSpec: { ...candidate(), [field]: value } })

    await expect(generateWorkflowAiDraft('modify', '修改', source)).rejects.toThrow(error)

    const validatedSpec = JSON.parse(wire.mock.calls[2][1].body).spec
    expect(validatedSpec.id).toBe(source.id)
    expect(validatedSpec.version).toBe(source.version)
    expect(wire).toHaveBeenCalledTimes(3)
    expect(source).toStrictEqual(before)
  })

  it('builds the review from normalizedSpec while retaining the baseline identity and layout', async () => {
    const normalizedSpec = candidate()
    normalizedSpec.title = '服务端规范化标题'
    normalizedSpec.nodes[0].executor.prompt = 'server-normalized prompt'
    normalizedSpec.nodes[0]._x = 999
    normalizedSpec.nodes[0]._y = 888
    wire.mockResolvedValueOnce(submitted())
      .mockResolvedValueOnce(task({ status: 'completed', result: { yaml: stringify(candidate()) } }))
      .mockResolvedValueOnce({ valid: true, issues: [], normalizedSpec })

    const draft = await generateWorkflowAiDraft('modify', '修改', source)

    expect(draft.workflowId).toBe(source.id)
    expect(draft.baseSignature).toBe(workflowSpecSignature(source))
    expect(draft.spec).toStrictEqual({
      ...normalizedSpec,
      nodes: normalizedSpec.nodes.map(node => node.id === 'analysis' ? { ...node, _x: 12, _y: 24 } : node),
    })
    expect(parse(draft.yaml).nodes[0].executor.prompt).toBe('server-normalized prompt')
    expect(draft.metadataChanges).toEqual(['title'])
    expect(draft.changes).toEqual([expect.objectContaining({ kind: 'update', nodeId: 'analysis' })])
  })

  it('fails closed when the AutoHeal/BaaS draft service is unavailable', async () => {
    capabilities.mockRejectedValueOnce(new Error('API 404: Not Found'))
    await expect(generateWorkflowAiDraft('modify', '修改', source)).rejects.toThrow('服务')
    expect(wire).not.toHaveBeenCalled()
  })

  it('surfaces generation failure and empty results', async () => {
    wire.mockResolvedValueOnce(submitted()).mockResolvedValueOnce(task({ status: 'failed', error: '模型不可用' }))
    await expect(generateWorkflowAiDraft('modify', '修改', source)).rejects.toThrow('模型不可用')
    wire.mockResolvedValueOnce(submitted()).mockResolvedValueOnce(task({ status: 'completed' }))
    await expect(generateWorkflowAiDraft('modify', '修改', source)).rejects.toThrow('YAML')
  })

  it.each([
    ['generation', 1],
    ['polling', 2],
    ['validation', 3],
  ])('aborts an in-flight %s request at the 15-minute deadline without further requests', async (_stage, requestCount) => {
    vi.useFakeTimers()
    const before = structuredClone(source)
    if (requestCount > 1) wire.mockResolvedValueOnce(submitted())
    if (requestCount > 2) {
      wire.mockResolvedValueOnce(task({ status: 'completed', result: { yaml: stringify(candidate()) } }))
    }
    let requestSignal: AbortSignal
    wire.mockImplementationOnce((_url, init) => new Promise((_resolve, reject) => {
      requestSignal = init.signal
      requestSignal.addEventListener('abort', () => reject(new Error('transport aborted')), { once: true })
    }))
    const promise = generateWorkflowAiDraft('modify', '修改', source)
    const rejection = expect(promise).rejects.toThrow('15 分钟')

    await vi.advanceTimersByTimeAsync(15 * 60_000 - 1)
    expect(requestSignal!.aborted).toBe(false)
    expect(wire).toHaveBeenCalledTimes(requestCount)
    expect(wire.mock.calls.every(([, init]) => init.signal === requestSignal!)).toBe(true)

    await vi.advanceTimersByTimeAsync(1)
    await rejection
    expect(requestSignal!.aborted).toBe(true)
    expect(requestSignal!.reason.message).toContain('15 分钟')
    expect(vi.getTimerCount()).toBe(0)
    await vi.advanceTimersByTimeAsync(30_000)
    expect(wire).toHaveBeenCalledTimes(requestCount)
    expect(source).toStrictEqual(before)
  })

  it('cancels during polling without further requests', async () => {
    vi.useFakeTimers()
    const controller = new AbortController()
    wire.mockResolvedValueOnce(submitted()).mockResolvedValueOnce(task({ status: 'running' }))
    const promise = generateWorkflowAiDraft('modify', '修改', source, controller.signal)
    const rejection = expect(promise).rejects.toThrow()
    await vi.advanceTimersByTimeAsync(1)
    controller.abort()
    await rejection
    await vi.advanceTimersByTimeAsync(30_000)
    expect(wire).toHaveBeenCalledTimes(2)
  })
})
