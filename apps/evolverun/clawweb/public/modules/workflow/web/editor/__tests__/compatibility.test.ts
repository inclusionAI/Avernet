import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { normalizeWorkflowSpec } from '../../../server/workflow'
import { useEditorStore } from '../store'

const state = () => useEditorStore.getState()
const jsonRoundtrip = <T,>(value: T): T => JSON.parse(JSON.stringify(value))
const values = () => ({ disabled: false, zero: 0, nullable: null, empty: '', textFalse: 'false', textZero: '0' })
const updatedPrompt = '{"instruction":"Keep this as text","enabled":false,"attempt":0,"optional":null}'

// Entirely synthetic fixtures: no saved production definition, endpoint or identity.
// Enter through the JSON boundary because the shared DTO cannot represent modern
// onResult, retry.backoffMs/on or outputContract.required as an array accurately.
function representativeSpec(onResult: unknown): WorkflowSpec {
  return jsonRoundtrip({
    id: 'synthetic-compatibility',
    version: '2.4',
    title: 'Synthetic workflow',
    config: { enabled: false, limit: 0, optional: null, future: values() },
    params: { request: '', count: 0, optional: null, enabled: false },
    requiredParams: ['request'],
    tests: [{ name: 'synthetic case', input: { request: '' }, expected: values() }],
    input: { mode: 'relaxed', requiredParams: ['request'], schema: { type: 'object', additionalProperties: false }, future: values() },
    identity: { key: '{{params.request}}', label: 'Synthetic request', duplicatePolicy: 'reject', future: values() },
    outputs: { approved: '{{nodeOutput.review.approved}}', score: '{{nodeOutput.review.score}}', future: values() },
    debug: { summaryKeys: [], future: values() },
    defaults: { contextPolicy: 'fresh', progress: '', user: '', future: values() },
    collaboration: { enabled: false, future: values() },
    workflow: { preflight: [], onStart: [], onFinish: [], future: values() },
    messages: { onCreated: '', onFinished: '', variants: [], future: values() },
    facade: { command: 'synthetic-compatibility', remark: '', future: values() },
    allowedBots: [],
    unknownFalse: false,
    unknownZero: 0,
    unknownNull: null,
    unknownTop: { nested: { values: [false, 0, null, '', { future: values() }] } },
    nodes: [
      {
        id: 'prepare', title: 'Synthetic preparation', phase: 'prepare',
        executor: { type: 'cli-script', command: 'echo', args: ['synthetic', ''], env: { SYNTHETIC_FLAG: '0' }, future: values() },
        // Existing serialized coordinates remain legacy input, not new UI writes.
        _x: 10, _y: 0,
      },
      {
        id: 'review', title: 'Synthetic review', phase: 'review', businessStatus: '',
        executor: { type: 'embedded-agent', prompt: 'Initial synthetic prompt', outputMode: 'json', context: { future: values() }, future: values() },
        dependsOn: ['prepare'],
        retry: { maxAttempts: 2, backoffMs: 0, on: ['timeout', 'error'], future: values() },
        outputContract: {
          type: 'object', required: ['approved', 'score'], additionalProperties: false,
          properties: { approved: { type: 'boolean', default: false }, score: { type: 'number', minimum: 0 }, note: { type: ['string', 'null'], default: null } },
          schema: { type: 'object', required: ['approved'], additionalProperties: false }, future: values(),
        },
        outputSchema: { type: 'object', additionalProperties: false, future: values() },
        mock: { output: { approved: false, score: 0, note: null }, future: values() },
        knowledge: false, knowledgeQuery: '', validationMinScore: 0,
        onSuccess: [{ id: 'synthetic-success', action: 'synthetic-action', required: false, args: { future: values() } }],
        onFailure: [],
        onFeedback: { target: 'prepare', feedbackPath: '', feedbackTemplate: '{{nodeOutput.review.note}}', future: values() },
        onResult,
        alerting: { dingtalk: false, keywords: [], future: values() },
        progressMessage: '',
        unknownFalse: false, unknownZero: 0, unknownNull: null,
        unknownNode: { nested: [values(), false, 0, null] },
      },
      {
        id: 'finish', title: 'Synthetic finish', phase: 'finish',
        executor: { type: 'done', future: values() },
      },
      {
        id: 'extension', title: 'Synthetic extension', phase: 'extension',
        executor: { type: 'synthetic-future-executor', payload: { nested: [values(), null, false, 0] } },
        dependsOn: ['prepare'], retry: { maxAttempts: 0, backoffMs: 0, on: [], future: values() },
        outputContract: { required: false, future: values() }, unknownNode: values(),
      },
    ],
  }) as unknown as WorkflowSpec
}

const routingCases: Array<{ name: string; onResult: unknown; branchId?: string }> = [
  {
    name: 'legacy target array',
    onResult: [{ value: 'approved', target: 'finish', future: values() }],
  },
  {
    name: 'legacy if/then/else object',
    onResult: {
      if: { approved: false, score: 0, note: null },
      then: { complete: false, rerun: { target: 'prepare', reset: 'target', feedbackTemplate: '{{nodeOutput.review.note}}', future: values() } },
      else: { complete: true }, future: values(),
    },
  },
  ...[false, true].map((complete) => ({
    name: `modern branch object with complete=${complete}`, branchId: 'proceed',
    onResult: {
      branches: [
        { branchId: 'proceed', match: { approved: false, score: 0, note: null }, complete, future: values() },
        { branchId: 'fallback', match: {}, complete: true, future: values() },
      ],
      default: { complete: false, future: values() }, future: values(),
    },
  })),
  { name: 'unknown routing object', onResult: { futureRouting: { nested: [values(), false, 0, null] } } },
  { name: 'explicit null routing', onResult: null },
  { name: 'unset routing', onResult: undefined },
]

beforeEach(() => { useEditorStore.setState(useEditorStore.getInitialState(), true) })
afterEach(() => { useEditorStore.setState(useEditorStore.getInitialState(), true) })

describe('synthetic editor-to-server normalization compatibility', () => {
  it.each(routingCases)('preserves the full $name document after real editor actions', ({ onResult, branchId }) => {
    const source = representativeSpec(onResult)
    const untouched = structuredClone(source)
    const expected = structuredClone(source)
    state().loadSpec(source)
    expect(state().actionError).toBeNull()
    expect(state().validationErrors).toEqual([])

    state().updateSpecField('title', 'Edited synthetic workflow')
    expect(state().actionError).toBeNull()
    expected.title = 'Edited synthetic workflow'
    state().updateNode('review', { title: 'Edited synthetic review' })
    expect(state().actionError).toBeNull()
    expected.nodes[1].title = 'Edited synthetic review'
    state().updateNode('review', { executor: { ...state().spec!.nodes[1].executor, prompt: updatedPrompt } })
    expect(state().actionError).toBeNull()
    expected.nodes[1].executor.prompt = updatedPrompt
    state().updateNode('extension', { title: 'Edited synthetic extension' })
    expect(state().actionError).toBeNull()
    expected.nodes[3].title = 'Edited synthetic extension'

    state().addEdge('prepare', 'finish')
    expect(state().actionError).toBeNull()
    state().addEdge('review', 'finish', branchId)
    expect(state().actionError).toBeNull()
    state().removeEdge('prepare', 'finish')
    expect(state().actionError).toBeNull()
    expected.nodes[2].dependsOn = ['review']
    if (branchId !== undefined) expected.nodes[2].branchId = branchId

    const documentBeforeMove = state().spec
    const dirtyBeforeMove = state().isDirty
    state().beginTransaction()
    state().moveNode('review', 0, -24)
    state().moveNode('prepare', 120, 80)
    state().endTransaction()
    expect(state().actionError).toBeNull()
    expect(state().spec).toBe(documentBeforeMove)
    expect(state().isDirty).toBe(dirtyBeforeMove)
    expect(state().positions.review).toEqual({ x: 0, y: -24 })
    expect(state().positions.prepare).toEqual({ x: 120, y: 80 })
    expect(state().spec!.nodes[1]).not.toHaveProperty('_x')
    expect(state().spec!.nodes[1]).not.toHaveProperty('_y')
    expect(state().spec!.nodes[0]).toMatchObject({ _x: 10, _y: 0 })
    expect(source).toStrictEqual(untouched)

    const submitted = jsonRoundtrip(state().spec!)
    expect(submitted).toStrictEqual(expected)
    const normalized = normalizeWorkflowSpec(submitted)
    // Assert against the independently patched original, not normalize(expected):
    // normalizing both sides would hide a lossy normalizer regression.
    const { version: normalizedVersion, ...normalizedDocument } = normalized
    const { version: expectedVersion, ...expectedDocument } = expected
    expect(normalizedDocument).toStrictEqual(expectedDocument)
    expect(normalizedVersion).toBe(expectedVersion)
    expect(jsonRoundtrip(normalized)).toStrictEqual(normalized)
    expect(submitted).toStrictEqual(expected)
    expect(normalized.nodes[1].executor.prompt).toBe(updatedPrompt)
    expect(typeof normalized.nodes[1].executor.prompt).toBe('string')
    expect(normalized.nodes[1].retry).toStrictEqual({ maxAttempts: 2, backoffMs: 0, on: ['timeout', 'error'], future: values() })
    expect(normalized.nodes[1].outputContract).toHaveProperty('required', ['approved', 'score'])
    expect(normalized.nodes[3].outputContract).toHaveProperty('required', false)
    expect(normalized.nodes[3].executor.type).toBe('synthetic-future-executor')
    if (onResult === undefined) expect(normalized.nodes[1]).not.toHaveProperty('onResult')
    else expect(normalized.nodes[1].onResult).toStrictEqual(onResult)
  })

  it.each([
    { name: 'numeric version', version: 7, expected: '7' },
    { name: 'numeric zero version', version: 0, expected: '0' },
    { name: 'trimmed string version', version: '  2.4  ', expected: '2.4' },
    { name: 'missing version', version: undefined, expected: '1' },
    { name: 'null version', version: null, expected: '1' },
  ])('documents server normalization of $name separately from field preservation', ({ version, expected }) => {
    const raw = { ...representativeSpec(routingCases[2].onResult), version }
    const source = jsonRoundtrip(raw) as unknown as WorkflowSpec
    state().loadSpec(source)
    state().updateNode('review', { title: 'Edited version fixture' })
    expect(state().actionError).toBeNull()
    const submitted = jsonRoundtrip(state().spec!)
    if (version === undefined) expect(submitted).not.toHaveProperty('version')
    else expect(submitted.version).toBe(version)

    const { version: beforeVersion, ...beforeDocument } = submitted
    const { version: afterVersion, ...afterDocument } = normalizeWorkflowSpec(submitted)
    expect(beforeVersion).toBe(version)
    expect(afterVersion).toBe(expected)
    expect(afterDocument).toStrictEqual(beforeDocument)
    // The server's conversion must not be mistaken for, or written back as,
    // lossless preservation of the editor's original version representation.
    expect(state().spec!.version).toBe(version)
  })
})
