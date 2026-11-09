// @vitest-environment jsdom
import { StrictMode } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import WorkflowSnapshotReconciliation from '../WorkflowSnapshotReconciliation'
import { assertWorkflowReconciliation, createWorkflowReconciliationOwner } from '../../api/workflowSnapshotReconciliation'
import type { ReconciliationContext } from '../../api/workflowSnapshotReconciliation'

afterEach(cleanup)
const context = (): ReconciliationContext => ({ workflowId: 'wf', packId: 'pack', sourceVersion: 'draft', sourceCommit: 'a'.repeat(40),
  expectedSavedHead: 'a'.repeat(40), expectedDraftToken: 'b'.repeat(64), workflowPath: 'workflow.yaml',
  dbSpecJson: '{"id":"wf","nodes":[]}', gitYaml: '# original\nid: wf\nx: &x !custom v\n',
  candidateSpecJson: '{"id":"wf","title":"edited","nodes":[]}',
  filesBefore: [{ path: 'workflow.yaml', contentBase64: btoa('# original\nid: wf\nx: &x !custom v\n'), mode: '100644' }],
  filesAfter: [{ path: 'workflow.yaml', contentBase64: btoa('# original\nid: wf\nx: &x !custom v\n'), mode: '100644' }], changes: [], revision: '1' })
function acknowledge() {
  fireEvent.click(screen.getByLabelText('我已审阅数据库原始文档'))
  fireEvent.click(screen.getByLabelText('我已审阅 Git 原始 YAML'))
  fireEvent.click(screen.getByLabelText('我已审阅最终文档及其他文件变更，理解 YAML 将被重新生成'))
}
it('shows complete originals and requires explicit selection and all acknowledgements', () => {
  const c = context(), review = createWorkflowReconciliationOwner().open(c), confirm = vi.fn()
  render(<WorkflowSnapshotReconciliation review={review} current={c} onConfirm={confirm} onCancel={() => {}} />)
  expect(screen.getByText(c.dbSpecJson)).toBeTruthy()
  expect([...document.querySelectorAll('pre')].some(node => node.textContent === c.gitYaml)).toBe(true)
  expect(screen.getByText(c.candidateSpecJson)).toBeTruthy()
  const button = screen.getByRole('button', { name: '确认最终文档' }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  fireEvent.click(screen.getByLabelText('使用数据库原始 JSON'))
  acknowledge()
  expect(button.disabled).toBe(false)
  fireEvent.click(button)
  expect(confirm).toHaveBeenCalledTimes(1)
  expect(() => assertWorkflowReconciliation(confirm.mock.calls[0][0], c, c.dbSpecJson)).not.toThrow()
})
it('rejects wrong identity and disables confirmation after changes', () => {
  const c = context(), review = createWorkflowReconciliationOwner().open(c), confirm = vi.fn()
  const view = render(<WorkflowSnapshotReconciliation review={review} current={c} onConfirm={confirm} onCancel={() => {}} />)
  fireEvent.click(screen.getByLabelText('手工编辑最终 JSON（可从候选文档开始）'))
  acknowledge()
  fireEvent.change(screen.getByLabelText('最终 JSON'), { target: { value: '{"id":"other"}' } })
  expect((screen.getByRole('button', { name: '确认最终文档' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByLabelText('我已审阅最终文档及其他文件变更，理解 YAML 将被重新生成'))
  fireEvent.click(screen.getByRole('button', { name: '确认最终文档' }))
  expect(confirm).not.toHaveBeenCalled()
  expect(screen.getByRole('alert').textContent).toContain('身份')
  view.rerender(<WorkflowSnapshotReconciliation review={review} current={{ ...c, revision: '2' }} onConfirm={confirm} onCancel={() => {}} />)
  expect((screen.getByRole('button', { name: '确认最终文档' }) as HTMLButtonElement).disabled).toBe(true)
})
it('resets acknowledgements on a new review and supports cancel', () => {
  const c = context(), confirm = vi.fn(), cancel = vi.fn()
  const view = render(<WorkflowSnapshotReconciliation review={createWorkflowReconciliationOwner().open(c)} current={c} onConfirm={confirm} onCancel={cancel} />)
  fireEvent.click(screen.getByLabelText('使用数据库原始 JSON')); acknowledge()
  view.rerender(<WorkflowSnapshotReconciliation review={createWorkflowReconciliationOwner().open(c)} current={c} onConfirm={confirm} onCancel={cancel} />)
  expect((screen.getByRole('button', { name: '确认最终文档' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '取消' }))
  expect(cancel).toHaveBeenCalledOnce()
})

it('shows complete prior and candidate file contents and modes including delete and create', () => {
  const c = context()
  c.filesBefore.push({ path: 'deleted.sh', contentBase64: btoa('old secret script\n'), mode: '100755' })
  c.filesAfter.push({ path: 'new.sh', contentBase64: btoa('new script\n'), mode: '100644' })
  c.filesBefore.push({ path: 'edited.sh', contentBase64: btoa('old text'), mode: '100755' })
  c.filesAfter.push({ path: 'edited.sh', contentBase64: btoa('new text'), mode: '100644' })
  c.changes.push({ operation: 'delete', path: 'deleted.sh' }, { operation: 'create', path: 'new.sh', contentBase64: btoa('new script\n'), mode: '100644' }, { operation: 'replace', path: 'edited.sh', contentBase64: btoa('new text'), mode: '100644' })
  render(<WorkflowSnapshotReconciliation review={createWorkflowReconciliationOwner().open(c)} current={c} onConfirm={() => {}} onCancel={() => {}} />)
  const old = screen.getByRole('region', { name: '原始文件: deleted.sh' })
  const added = screen.getByRole('region', { name: '候选文件: new.sh' })
  expect(old.textContent).toContain('old secret script'); expect(old.textContent).toContain('100755')
  expect(added.textContent).toContain('new script'); expect(added.textContent).toContain('100644')
  expect(screen.queryByRole('region', { name: '候选文件: deleted.sh' })).toBeNull()
  expect(screen.getByRole('region', { name: '原始文件: edited.sh' }).textContent).toContain('old text')
  expect(screen.getByRole('region', { name: '候选文件: edited.sh' }).textContent).toContain('new text')
})
it('warns and blocks confirmation when binary bytes cannot be reviewed', () => {
  const c = context()
  c.filesBefore.push({ path: 'binary', mode: '100644', contentBase64: 'AP8=' })
  c.filesAfter.push({ path: 'binary', mode: '100644', contentBase64: 'AP8=' })
  const confirm = vi.fn()
  render(<WorkflowSnapshotReconciliation review={createWorkflowReconciliationOwner().open(c)} current={c} onConfirm={confirm} onCancel={() => {}} />)
  expect(screen.getByRole('alert').textContent).toContain('不能声称已阅读')
  fireEvent.click(screen.getByLabelText('使用数据库原始 JSON')); acknowledge()
  expect((screen.getByRole('button', { name: '确认最终文档' }) as HTMLButtonElement).disabled).toBe(true)
  expect(confirm).not.toHaveBeenCalled()
})
it('revokes an issued approval on editing, replacement from a different owner, and cancel', () => {
  const c = context(), owner = createWorkflowReconciliationOwner(), review = owner.open(c), confirm = vi.fn()
  const view = render(<WorkflowSnapshotReconciliation review={review} current={c} onConfirm={confirm} onCancel={() => {}} />)
  fireEvent.click(screen.getByLabelText('手工编辑最终 JSON（可从候选文档开始）')); acknowledge()
  fireEvent.click(screen.getByRole('button', { name: '确认最终文档' }))
  const first = confirm.mock.calls[0][0]
  fireEvent.change(screen.getByLabelText('最终 JSON'), { target: { value: c.dbSpecJson } })
  expect(() => assertWorkflowReconciliation(first, c, c.candidateSpecJson)).toThrow()
  fireEvent.click(screen.getByLabelText('我已审阅最终文档及其他文件变更，理解 YAML 将被重新生成'))
  fireEvent.click(screen.getByRole('button', { name: '确认最终文档' }))
  const second = confirm.mock.calls[1][0]
  const newReview = createWorkflowReconciliationOwner().open(c)
  view.rerender(<WorkflowSnapshotReconciliation review={newReview} current={c} onConfirm={confirm} onCancel={() => {}} />)
  expect(() => assertWorkflowReconciliation(second, c, c.dbSpecJson)).toThrow()
  fireEvent.click(screen.getByLabelText('使用数据库原始 JSON')); acknowledge()
  fireEvent.click(screen.getByRole('button', { name: '确认最终文档' }))
  const third = confirm.mock.calls[2][0]
  fireEvent.click(screen.getByRole('button', { name: '取消' }))
  expect(() => assertWorkflowReconciliation(third, c, c.dbSpecJson)).toThrow()
})

it('revokes approval when current props change even if they are later restored', () => {
  const c = context(), review = createWorkflowReconciliationOwner().open(c), confirm = vi.fn()
  const view = render(<WorkflowSnapshotReconciliation review={review} current={c} onConfirm={confirm} onCancel={() => {}} />)
  fireEvent.click(screen.getByLabelText('使用数据库原始 JSON')); acknowledge()
  fireEvent.click(screen.getByRole('button', { name: '确认最终文档' }))
  const approval = confirm.mock.calls[0][0]
  view.rerender(<WorkflowSnapshotReconciliation review={review} current={{ ...c, revision: '2' }} onConfirm={confirm} onCancel={() => {}} />)
  view.rerender(<WorkflowSnapshotReconciliation review={review} current={c} onConfirm={confirm} onCancel={() => {}} />)
  expect(() => assertWorkflowReconciliation(approval, c, c.dbSpecJson)).toThrow()
})

it('revokes on unmount without breaking StrictMode mount', () => {
  const c = context(), review = createWorkflowReconciliationOwner().open(c), confirm = vi.fn()
  const view = render(<StrictMode><WorkflowSnapshotReconciliation review={review} current={c} onConfirm={confirm} onCancel={() => {}} /></StrictMode>)
  fireEvent.click(screen.getByLabelText('使用数据库原始 JSON')); acknowledge()
  fireEvent.click(screen.getByRole('button', { name: '确认最终文档' }))
  expect(confirm).toHaveBeenCalledTimes(1)
  const proof = confirm.mock.calls[0][0]
  expect(() => assertWorkflowReconciliation(proof, c, c.dbSpecJson)).not.toThrow()
  view.unmount()
  expect(() => assertWorkflowReconciliation(proof, c, c.dbSpecJson)).toThrow()
})

it('permanently disables a stale review after props revert and requires a fresh review and acknowledgements', () => {
  const c = context(), owner = createWorkflowReconciliationOwner(), review = owner.open(c), confirm = vi.fn()
  const view = render(<WorkflowSnapshotReconciliation review={review} current={c} onConfirm={confirm} onCancel={() => {}} />)
  fireEvent.click(screen.getByLabelText('使用数据库原始 JSON')); acknowledge()
  view.rerender(<WorkflowSnapshotReconciliation review={review} current={{ ...c, revision: '2' }} onConfirm={confirm} onCancel={() => {}} />)
  view.rerender(<WorkflowSnapshotReconciliation review={review} current={c} onConfirm={confirm} onCancel={() => {}} />)
  const button = screen.getByRole('button', { name: '确认最终文档' }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  fireEvent.click(button)
  expect(confirm).not.toHaveBeenCalled()
  expect(screen.getByRole('alert').textContent).toContain('重新核对')
  const fresh = owner.open(c)
  view.rerender(<WorkflowSnapshotReconciliation review={fresh} current={c} onConfirm={confirm} onCancel={() => {}} />)
  expect((screen.getByRole('button', { name: '确认最终文档' }) as HTMLButtonElement).disabled).toBe(true)
  expect((screen.getByLabelText('我已审阅数据库原始文档') as HTMLInputElement).checked).toBe(false)
  fireEvent.click(screen.getByLabelText('使用数据库原始 JSON')); acknowledge()
  fireEvent.click(screen.getByRole('button', { name: '确认最终文档' }))
  expect(confirm).toHaveBeenCalledTimes(1)
})
it.each(['\u00ad', '\u061c'])('warns and blocks confirmation for invisible file character %j', char => {
  const c = context()
  const file = { path: 'script', mode: '100644' as const, contentBase64: Buffer.from(`a${char}b`, 'utf8').toString('base64') }
  c.filesBefore.push(file); c.filesAfter.push(file)
  const confirm = vi.fn()
  render(<WorkflowSnapshotReconciliation review={createWorkflowReconciliationOwner().open(c)} current={c} onConfirm={confirm} onCancel={() => {}} />)
  expect(screen.getByRole('alert').textContent).toContain('不能声称已阅读')
  fireEvent.click(screen.getByLabelText('使用数据库原始 JSON')); acknowledge()
  expect((screen.getByRole('button', { name: '确认最终文档' }) as HTMLButtonElement).disabled).toBe(true)
  expect(confirm).not.toHaveBeenCalled()
})

it.each(['\u00ad', '\u061c'])('warns and blocks when DB review text contains invisible %j', char => {
  const c = context()
  c.dbSpecJson = `{"id":"wf","title":"a${char}b","nodes":[]}`
  const confirm = vi.fn()
  render(<WorkflowSnapshotReconciliation review={createWorkflowReconciliationOwner().open(c)} current={c} onConfirm={confirm} onCancel={() => {}} />)
  expect(screen.getByRole('alert').textContent).toContain('不可安全显示')
  fireEvent.click(screen.getByLabelText('使用数据库原始 JSON')); acknowledge()
  expect((screen.getByRole('button', { name: '确认最终文档' }) as HTMLButtonElement).disabled).toBe(true)
  expect(confirm).not.toHaveBeenCalled()
})

it('previews the selected final YAML and makes the preserved scripts explicit', () => {
  const c = context(), script = { path: 'scripts/run.sh', contentBase64: btoa('echo keep\n'), mode: '100755' as const }
  c.filesBefore.push(script); c.filesAfter.push(script)
  const confirm = vi.fn()
  render(<WorkflowSnapshotReconciliation review={createWorkflowReconciliationOwner().open(c)} current={c} onConfirm={confirm} onCancel={() => {}} />)
  expect(screen.getByText(/数据库草稿是已持久化的最新真值/)).toBeVisible()
  expect(screen.getByText(/脚本和其他 Pack 文件按候选快照原样保留/)).toBeVisible()
  fireEvent.click(screen.getByLabelText('使用数据库原始 JSON'))
  const preview = screen.getByRole('region', { name: '将保存的 YAML 预览' })
  expect(preview.textContent).toContain('id: wf'); expect(preview.textContent).not.toContain('title: edited')
  fireEvent.click(screen.getByLabelText('手工编辑最终 JSON（可从候选文档开始）'))
  expect(preview.textContent).toContain('title: edited')
  acknowledge(); fireEvent.click(screen.getByRole('button', { name: '确认最终文档' }))
  expect(() => assertWorkflowReconciliation(confirm.mock.calls[0][0], c, c.candidateSpecJson)).not.toThrow()
  expect(c.filesAfter.find(f => f.path === 'scripts/run.sh')).toEqual(script)
})
