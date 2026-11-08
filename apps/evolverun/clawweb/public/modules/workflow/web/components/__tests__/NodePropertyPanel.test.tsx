import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowNode } from '@avernet/clawweb-shared/web/types'
import NodePropertyPanel from '../NodePropertyPanel'
import { useEditorStore } from '../../editor/store'
import { createEditorNode, getNodeMeta, NODE_CATALOG } from '../../editor/nodeCatalog'

const node = (executor: WorkflowNode['executor'] = { type: 'embedded-agent', prompt: '原指令' }, extra: Record<string, unknown> = {}): WorkflowNode =>
  ({ id: 'a', title: '业务名称', phase: 'P1', executor, ...extra })
function load(first = node(), others: WorkflowNode[] = []) {
  useEditorStore.getState().loadSpec({ id: 'test', title: '测试流程', version: '1', nodes: [first, ...others] })
  useEditorStore.getState().selectNode(first.id)
  return render(<NodePropertyPanel />)
}
const current = () => useEditorStore.getState().spec!.nodes[0]
const edit = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })
const apply = (label: string) => fireEvent.click(screen.getByRole('button', { name: `应用 ${label}` }))
const details = (label: string) => fireEvent.click(screen.getByText(new RegExp(`^${label}`), { selector: 'summary' }))

beforeEach(() => {
  for (const key of Object.keys(useEditorStore.getState().pendingEdits)) useEditorStore.getState().setPendingEdit(key, null)
  useEditorStore.getState().markClean()
  useEditorStore.getState().reset()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('minimal node catalog', () => {
  it('provides public, empty templates and stable Chinese labels without implicit defaults', () => {
    const ids = new Set<string>()
    for (const meta of NODE_CATALOG) {
      const created = createEditorNode(meta.type)
      expect(created.phase).toBe('P1')
      expect(created.title).toBe(meta.label)
      expect(created.executor.type).toBe(meta.type)
      expect(created).not.toHaveProperty('retry')
      expect(created).not.toHaveProperty('triggerRule')
      expect(created).not.toHaveProperty('_x')
      expect(created.executor).not.toHaveProperty('outputMode')
      expect(Object.values(created.executor).slice(1).every(value => value === '')).toBe(true)
      ids.add(created.id)
    }
    expect(ids.size).toBe(NODE_CATALOG.length)
    expect(createEditorNode('done').id).not.toBe(createEditorNode('done').id)
    expect(createEditorNode('done', { x: 3, y: 8 })).toMatchObject({ _x: 3, _y: 8 })
    expect(getNodeMeta('future-tool').label).toBe('future-tool')
    expect(createEditorNode('future-tool').executor).toEqual({ type: 'future-tool' })
    expect(createEditorNode('future-tool').title).toBe('扩展节点（future-tool）')
  })
})

describe('NodePropertyPanel with real store (no business API hooks)', () => {
  it('does not materialize defaults and treats JSON-looking text as literal strings', () => {
    const original = node({ type: 'embedded-agent', prompt: '原指令', custom: { enabled: false }, timeoutMs: 0 },
      { retry: { maxAttempts: 2, custom: null }, outputContract: { required: ['ok'], extra: false }, customNode: 0 })
    load(original)
    expect(current()).toEqual(original)
    edit('任务指令', '{"this":"is text"}')
    edit('节点名称', '新名称')
    expect(current()).toEqual({ ...original, title: '新名称', executor: { ...original.executor, prompt: '{"this":"is text"}' } })
    expect(screen.getByLabelText('节点 ID')).toHaveAttribute('readonly')
    expect(current().executor).not.toHaveProperty('outputMode')
    expect(screen.queryByLabelText('执行器类型')).not.toBeInTheDocument()
  })

  it('edits a command and each argument without splitting spaces or losing env', () => {
    load(node({ type: 'cli-script', command: '/a directory/my executable', args: ['a b', '--flag', ''], env: { KEEP: 'yes' }, workdir: '/work' }))
    edit('命令或脚本路径', 'a command with spaces')
    edit('参数 1', '{"literal": true}')
    expect(current().executor).toEqual({ type: 'cli-script', command: 'a command with spaces', args: ['{"literal": true}', '--flag', ''], env: { KEEP: 'yes' }, workdir: '/work' })
    details('原始参数 JSON')
    edit('完整参数', '{"named":"has spaces"}')
    expect(Array.isArray(current().executor.args)).toBe(true)
    apply('完整参数')
    expect(current().executor.args).toEqual({ named: 'has spaces' })
  })

  it('guards switching, closing, copying and deleting while valid JSON is unapplied', () => {
    load(node({ type: 'mcp-call', server: '', tool: '', args: { n: 1 } }), [node({ type: 'done' }, { id: 'b' })])
    edit('工具参数', '{"n":2}')
    expect(screen.getByRole('button', { name: '关闭节点面板' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '复制节点' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '删除节点' })).toBeDisabled()
    act(() => useEditorStore.getState().selectNode('b'))
    expect(useEditorStore.getState().selectedNodeId).toBe('a')
    expect(screen.getByLabelText('工具参数')).toHaveValue('{"n":2}')
    apply('工具参数')
    expect(current().executor.args).toEqual({ n: 2 })
    fireEvent.click(screen.getByRole('button', { name: '关闭节点面板' }))
    expect(useEditorStore.getState().selectedNodeId).toBeNull()
  })

  it('uses the complete raw object, retains unknown fields, and disallows changing id or type', () => {
    const original = node({ type: 'future', secretlessExtension: { a: false, b: null } }, { vendor: { enabled: false }, retry: 0 })
    load(original)
    details('完整节点 JSON')
    edit('完整节点配置', JSON.stringify({ ...original, id: 'changed' }))
    expect(screen.getByRole('button', { name: '应用 完整节点配置' })).toBeDisabled()
    expect(current()).toEqual(original)
    edit('完整节点配置', JSON.stringify({ ...original, executor: { type: 'done' } }))
    expect(screen.getByRole('button', { name: '应用 完整节点配置' })).toBeDisabled()
    edit('完整节点配置', JSON.stringify({ ...original, title: '新名称' }))
    apply('完整节点配置')
    expect(current()).toEqual({ ...original, title: '新名称' })
  })

  it('requires explicit confirmation for omitted fields and applies top-level deletions through a shallow patch', () => {
    const original = node({ type: 'embedded-agent', prompt: '', custom: { a: 1, b: 2 } }, { retry: { maxAttempts: 2 }, vendor: null })
    load(original)
    details('完整节点 JSON')
    const { retry: _retry, ...next } = original
    next.executor = { ...original.executor, custom: { a: 1 } }
    edit('完整节点配置', JSON.stringify(next))
    vi.mocked(window.confirm).mockReturnValueOnce(false)
    apply('完整节点配置')
    expect(current()).toEqual(original)
    expect(useEditorStore.getState().pendingEdits['node:a:raw']).toBeTruthy()
    apply('完整节点配置')
    expect(current().retry).toBeUndefined()
    expect(JSON.parse(JSON.stringify(current()))).toEqual(next)
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('$.retry'))
  })

  it('keeps both timeout units and the entire output contract/retry configuration', () => {
    const original = node({ type: 'cli-script', command: '', timeoutMs: 0, timeoutSeconds: 12 },
      { retry: { maxAttempts: 3, on: ['timeout'], vendor: false }, outputContract: { required: ['score'], schema: { type: 'object' } } })
    load(original)
    details('高级配置')
    expect(screen.getByLabelText('执行超时（毫秒）')).toHaveValue('0')
    expect(screen.getByLabelText('执行超时（秒）')).toHaveValue('12')
    edit('执行超时（秒）', '24')
    apply('执行超时（秒）')
    expect(current()).toEqual({ ...original, executor: { ...original.executor, timeoutSeconds: 24 } })
    expect(screen.getByLabelText('失败重试')).toHaveValue(JSON.stringify(original.retry, null, 2))
    expect(screen.getByLabelText('输出契约')).toHaveValue(JSON.stringify(original.outputContract, null, 2))
  })

  it.each([
    ['human', '希望用户做什么', 'prompt'], ['approval', '审批内容', 'message'],
    ['done', '完成文案', 'message'], ['subagent', '任务指令', 'prompt'],
  ])('exposes business fields for %s and preserves extensions', (type, label, field) => {
    load(node({ type, [field]: '', skillName: '', inputSchema: { fields: [] }, actions: { revise: {} }, saveAs: { a: 'b' }, delivery: { channel: 'custom' } }))
    edit(label, '[literal text]')
    expect(current().executor[field]).toBe('[literal text]')
    expect(current().executor.saveAs).toEqual({ a: 'b' })
    expect(current().executor.actions).toEqual({ revise: {} })
  })

  it('confirms deletion, defers references to the real store and shows its rejection', () => {
    load(node(), [node({ type: 'embedded-agent', prompt: '{{nodes.a.output}}' }, { id: 'b' })])
    fireEvent.click(screen.getByRole('button', { name: '删除节点' }))
    expect(window.confirm).toHaveBeenCalled()
    expect(useEditorStore.getState().spec!.nodes).toHaveLength(2)
    expect(useEditorStore.getState().actionError).toBeTruthy()
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })

  it.each([true, false])('preserves complete=%s on referenced branches without presenting a termination control', complete => {
    load(node(undefined, { onResult: { extension: false, branches: [
      { branchId: 'yes', match: { ok: true }, complete, custom: { keep: null } }, { branchId: 'otherwise', match: {} },
    ] } }), [node({ type: 'done' }, { id: 'b', dependsOn: ['a'], branchId: 'yes' })])
    const branch = screen.getByRole('group', { name: '分支 yes' })
    expect(within(branch).queryByRole('checkbox')).not.toBeInTheDocument()
    expect(within(branch).getByLabelText('分支标识')).toHaveAttribute('readonly')
    expect(within(branch).getByRole('button', { name: '删除分支' })).toBeDisabled()
    fireEvent.change(within(branch).getByLabelText('匹配条件'), { target: { value: '{"ok":"true"}' } })
    fireEvent.click(within(branch).getByRole('button', { name: '应用 匹配条件' }))
    expect(current().onResult).toEqual({ extension: false, branches: [
      { branchId: 'yes', match: { ok: 'true' }, complete, custom: { keep: null } }, { branchId: 'otherwise', match: {} },
    ] })
    expect(screen.getByText(/按顺序首个匹配生效/)).toBeInTheDocument()
    expect(useEditorStore.getState().spec!.nodes[1]).toMatchObject({ branchId: 'yes', dependsOn: ['a'] })
    expect(useEditorStore.getState().actionError).toBeNull()
  })

  it.each([{ legacy: [{ value: 'yes', target: 'b' }] }, { legacy: { if: 'ok', then: { complete: true } } }])('keeps legacy routing untouched: %j', ({ legacy }) => {
    load(node(undefined, { onResult: legacy }))
    expect(screen.getByText(/保留原始配置模式/)).toBeInTheDocument()
    expect(current().onResult).toEqual(legacy)
    expect(screen.queryByRole('button', { name: '添加结果分支' })).not.toBeInTheDocument()
  })
})


describe('editing guard and branch regressions', () => {
  it('keeps malformed JSON pending and blocks copy/delete until explicitly discarded', () => {
    load()
    details('完整节点 JSON')
    edit('完整节点配置', '{broken')
    expect(screen.getByRole('button', { name: '应用 完整节点配置' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '复制节点' }))
    expect(useEditorStore.getState().spec!.nodes).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: '放弃 完整节点配置' }))
    fireEvent.click(screen.getByRole('button', { name: '复制节点' }))
    expect(useEditorStore.getState().spec!.nodes).toHaveLength(2)
    expect(useEditorStore.getState().selectedNodeId).not.toBe('a')
  })

  it('does not lose drafts when a section is collapsed and restores pending on a rejected full update', () => {
    load()
    details('完整节点 JSON')
    const text = JSON.stringify({ ...current(), dependsOn: ['a'] })
    edit('完整节点配置', text)
    details('完整节点 JSON')
    expect(useEditorStore.getState().pendingEdits['node:a:raw']).toBeTruthy()
    details('完整节点 JSON')
    apply('完整节点配置')
    expect(current()).not.toHaveProperty('dependsOn')
    expect(screen.getByLabelText('完整节点配置')).toHaveValue(text)
    expect(useEditorStore.getState().pendingEdits['node:a:raw']).toBeTruthy()
    expect(useEditorStore.getState().actionError).toBeTruthy()
  })

  it('can resolve two pending JSON edits without silently discarding either', () => {
    load(node({ type: 'mcp-call', server: '', tool: '', args: {} }))
    edit('工具参数', '{"mine":1}')
    details('高级配置')
    edit('失败重试', '{"maxAttempts":2}')
    apply('工具参数')
    expect(current().executor.args).toEqual({})
    expect(useEditorStore.getState().pendingEdits).toHaveProperty('node:a:executor.args')
    expect(useEditorStore.getState().pendingEdits).toHaveProperty('node:a:retry')
    fireEvent.click(screen.getByRole('button', { name: '放弃 失败重试' }))
    apply('工具参数')
    expect(current().executor.args).toEqual({ mine: 1 })
    expect(useEditorStore.getState().pendingEdits).toEqual({})
  })

  it('keeps missing output modes and legacy trigger rules until explicit selection/removal', () => {
    load(node({ type: 'embedded-agent', prompt: '' }, { join: 'any' }))
    expect(screen.getByText(/有效规则：one_success/)).toHaveTextContent('兼容 join:any')
    expect(current()).not.toHaveProperty('triggerRule')
    edit('输出形式', JSON.stringify('text'))
    expect(current().executor.outputMode).toBe('text')
    vi.mocked(window.confirm).mockReturnValueOnce(false)
    edit('输出形式', 'missing')
    expect(current().executor.outputMode).toBe('text')
    edit('输出形式', 'missing')
    expect(current().executor).not.toHaveProperty('outputMode')
    edit('触发规则', JSON.stringify('all_done'))
    expect(current().triggerRule).toBe('all_done')
    expect(current().join).toBe('any')
  })

  it('retains false/null/empty extension values and unknown output modes', () => {
    const original = node({ type: 'embedded-agent', prompt: null, skillName: '', outputMode: 'future', contextPolicy: false },
      { retry: null, vendor: '', outputContract: false, phase: 'P2' })
    load(original)
    expect(screen.getByLabelText('任务指令')).toHaveAttribute('readonly')
    expect(screen.getByLabelText('输出形式')).toHaveValue(JSON.stringify('future'))
    edit('节点名称', '新名称')
    expect(current()).toEqual({ ...original, title: '新名称' })
    expect(screen.getByText(/^高级配置/, { selector: 'summary' })).toHaveTextContent('retry=null')
  })

  it('edits original timeout units without coercion, and explicitly removes retry', () => {
    load(node({ type: 'bcs-route', target: '', message: '', timeout: '30m' }, { retry: false }))
    details('高级配置')
    expect(screen.getByLabelText('执行超时（原始单位）')).toHaveValue('"30m"')
    edit('执行超时（原始单位）', '"1h"')
    apply('执行超时（原始单位）')
    expect(current().executor.timeout).toBe('1h')
    fireEvent.click(screen.getByRole('button', { name: '删除 失败重试' }))
    expect(current().retry).toBeUndefined()
  })

  it('preserves imported coordinates when applying a full node and rejects coordinate changes', () => {
    load(node(undefined, { _x: 12, _y: 8 }))
    details('完整节点 JSON')
    edit('完整节点配置', JSON.stringify({ ...current(), title: 'changed' }))
    apply('完整节点配置')
    expect(current()).toMatchObject({ title: 'changed', _x: 12, _y: 8 })
    edit('完整节点配置', JSON.stringify({ ...current(), _x: 99 }))
    expect(screen.getByRole('button', { name: '应用 完整节点配置' })).toBeDisabled()
  })

  it('edits human actions and approval configuration without injecting defaults', () => {
    load(node({ type: 'human', prompt: '', actions: { confirm: { label: '继续' }, reject: { target: 'a' } } }))
    fireEvent.click(screen.getByText(/确认、修订与拒绝动作/, { selector: 'summary' }))
    const next = { confirm: { label: '确认', vendor: false }, reject: { target: 'a' } }
    edit('确认、修订与拒绝动作', JSON.stringify(next))
    apply('确认、修订与拒绝动作')
    expect(current().executor.actions).toEqual(next)
    expect(current().executor).not.toHaveProperty('saveAs')
  })

  it('adds and removes simple args explicitly while preserving a blank argument', () => {
    load(node({ type: 'cli-script', command: '' }))
    fireEvent.click(screen.getByRole('button', { name: '添加参数' }))
    expect(current().executor.args).toEqual([''])
    fireEvent.click(screen.getByRole('button', { name: '添加参数' }))
    edit('参数 2', 'with spaces')
    fireEvent.click(screen.getByRole('button', { name: '删除参数 1' }))
    expect(current().executor.args).toEqual(['with spaces'])
    expect(current().executor.command).toBe('')
  })

  it('still blocks deleting or renaming a referenced branch with complete=true through raw JSON', () => {
    load(node(undefined, { onResult: { branches: [{ branchId: 'yes', match: { value: true }, complete: true }] } }),
      [node({ type: 'done' }, { id: 'b', branchId: 'yes', dependsOn: ['a'] })])
    details('完整节点 JSON')
    for (const branches of [[], [{ branchId: 'other', match: {}, complete: true }]]) {
      edit('完整节点配置', JSON.stringify({ ...current(), onResult: { branches } }))
      expect(screen.getByRole('button', { name: '应用 完整节点配置' })).toBeDisabled()
    }
    expect(useEditorStore.getState().spec!.nodes[1].branchId).toBe('yes')
  })

  it('allows complete to change in raw routing and full-node JSON without breaking downstream membership', () => {
    const route = (complete: boolean) => ({ extension: false, branches: [
      { branchId: 'yes', match: { ready: true }, complete, custom: { keep: null } },
    ] })
    load(node(undefined, { onResult: route(false) }),
      [node({ type: 'done' }, { id: 'b', branchId: 'yes', dependsOn: ['a'] })])
    details('完整结果路由 JSON')
    edit('完整结果路由', JSON.stringify(route(true)))
    expect(screen.getByRole('button', { name: '应用 完整结果路由' })).toBeEnabled()
    apply('完整结果路由')
    expect(current().onResult).toEqual(route(true))
    expect(useEditorStore.getState().pendingEdits).toEqual({})

    details('完整节点 JSON')
    for (const complete of [false, true]) {
      edit('完整节点配置', JSON.stringify({ ...current(), onResult: route(complete) }))
      expect(screen.getByRole('button', { name: '应用 完整节点配置' })).toBeEnabled()
      apply('完整节点配置')
      expect(current().onResult).toEqual(route(complete))
      expect(useEditorStore.getState().spec!.nodes[1]).toMatchObject({ branchId: 'yes', dependsOn: ['a'] })
      expect(useEditorStore.getState().pendingEdits).toEqual({})
      expect(useEditorStore.getState().actionError).toBeNull()
    }
    expect(screen.queryByLabelText('命中后完成此分支')).not.toBeInTheDocument()
  })

  it('creates a typed branch before the fallback without losing routing extensions', () => {
    load(node(undefined, { onResult: { vendor: null, branches: [{ branchId: 'fallback', match: {}, custom: false }] } }))
    fireEvent.click(screen.getByRole('button', { name: '添加结果分支' }))
    edit('新分支配置', JSON.stringify({ branchId: 'ready', match: { ready: true, count: 2, absent: null }, complete: false, custom: 0 }))
    apply('新分支配置')
    expect(current().onResult).toEqual({ vendor: null, branches: [
      { branchId: 'ready', match: { ready: true, count: 2, absent: null }, complete: false, custom: 0 },
      { branchId: 'fallback', match: {}, custom: false },
    ] })
  })

  it('reorders non-default branches, blocks moving defaults early, and confirms deletion', () => {
    load(node(undefined, { onResult: { branches: [
      { branchId: 'one', match: { x: 1 }, extra: 1 }, { branchId: 'two', match: { x: 2 } }, { branchId: 'fallback', match: {} },
    ] } }))
    fireEvent.click(within(screen.getByRole('group', { name: '分支 one' })).getByRole('button', { name: '下移' }))
    expect((current().onResult as unknown as { branches: { branchId: string }[] }).branches.map(branch => branch.branchId)).toEqual(['two', 'one', 'fallback'])
    expect(within(screen.getByRole('group', { name: '分支 fallback' })).getByRole('button', { name: '上移' })).toBeDisabled()
    const branch = within(screen.getByRole('group', { name: '分支 one' }))
    fireEvent.change(branch.getByLabelText('匹配条件'), { target: { value: '{}' } })
    expect(branch.getByRole('button', { name: '应用 匹配条件' })).toBeDisabled()
    fireEvent.click(branch.getByRole('button', { name: '放弃 匹配条件' }))
    fireEvent.click(branch.getByRole('button', { name: '删除分支' }))
    expect((current().onResult as unknown as { branches: unknown[] }).branches).toHaveLength(2)
  })

  it('refuses duplicate ids and non-scalar match values for a new branch', () => {
    load(node(undefined, { onResult: { branches: [{ branchId: 'existing', match: {} }] } }))
    fireEvent.click(screen.getByRole('button', { name: '添加结果分支' }))
    for (const value of [{ branchId: 'existing', match: { ok: true } }, { branchId: 'fresh', match: { ok: [] } }, { match: {} }]) {
      edit('新分支配置', JSON.stringify(value))
      expect(screen.getByRole('button', { name: '应用 新分支配置' })).toBeDisabled()
    }
    fireEvent.click(screen.getByRole('button', { name: '放弃 新分支配置' }))
    fireEvent.click(screen.getByRole('button', { name: '取消新增分支' }))
    expect(screen.queryByLabelText('新分支配置')).not.toBeInTheDocument()
    expect(useEditorStore.getState().pendingEdits).toEqual({})
  })
})
