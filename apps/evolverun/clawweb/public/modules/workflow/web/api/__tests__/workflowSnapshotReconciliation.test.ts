import { describe, expect, it } from 'vitest'
import { assertWorkflowReconciliation, confirmWorkflowReconciliation, createWorkflowReconciliationOwner, invalidateWorkflowReconciliation, workflowReconciliationIsCurrent } from '../workflowSnapshotReconciliation'
import type { ReconciliationContext } from '../workflowSnapshotReconciliation'

const context = (): ReconciliationContext => ({ workflowId: 'wf', packId: 'pack', sourceVersion: 'draft', sourceCommit: 'a'.repeat(40),
  expectedSavedHead: 'a'.repeat(40), expectedDraftToken: 'b'.repeat(64), workflowPath: 'workflow.yaml',
  dbSpecJson: '{"id":"wf","nodes":[]}', gitYaml: 'id: wf\nvalue: &anchor !custom value\n',
  candidateSpecJson: '{"id":"wf","nodes":[],"title":"draft"}',
  filesBefore: [{ path: 'workflow.yaml', contentBase64: btoa('id: wf\nvalue: &anchor !custom value\n'), mode: '100644' }, { path: 'x', contentBase64: 'eQ==', mode: '100755' }],
  filesAfter: [{ path: 'workflow.yaml', contentBase64: btoa('id: wf\nvalue: &anchor !custom value\n'), mode: '100644' }, { path: 'x', contentBase64: 'eA==', mode: '100644' }], changes: [{ operation: 'replace', path: 'x', contentBase64: 'eA==', mode: '100644' }], revision: 'session:1' })

describe('snapshot reconciliation binding', () => {
  it('retains raw YAML without parsing or claiming it can be represented', () => {
    const c = context(), review = createWorkflowReconciliationOwner().open(c)
    expect(review.gitYaml).toBe(c.gitYaml)
    const proof = confirmWorkflowReconciliation(review, c, c.candidateSpecJson)
    expect(() => assertWorkflowReconciliation(proof, c, c.candidateSpecJson)).not.toThrow()
    expect(() => assertWorkflowReconciliation({ specJson: c.candidateSpecJson }, c, c.candidateSpecJson)).toThrow()
    expect(() => assertWorkflowReconciliation(proof, c, c.dbSpecJson)).toThrow()
  })
  it.each([
    ['workflowId', 'other'], ['packId', 'other'], ['sourceCommit', 'c'.repeat(40)], ['expectedSavedHead', 'c'.repeat(40)],
    ['expectedDraftToken', 'c'.repeat(64)], ['sourceVersion', 'current'], ['workflowPath', 'other.yaml'],
    ['dbSpecJson', '{"id":"wf","nodes":[],"x":1}'], ['gitYaml', 'changed'],
    ['candidateSpecJson', '{}'], ['revision', 'session:2'],
  ] as const)('rejects changed %s after review', (field, value) => {
    const c = context(), review = createWorkflowReconciliationOwner().open(c), proof = confirmWorkflowReconciliation(review, c, c.dbSpecJson)
    const changed = { ...c, [field]: value }
    expect(workflowReconciliationIsCurrent(review, changed)).toBe(false)
    expect(() => assertWorkflowReconciliation(proof, changed, c.dbSpecJson)).toThrow()
  })
  it('revokes a cancelled review and any approval issued from it', () => {
    const c = context(), review = createWorkflowReconciliationOwner().open(c), proof = confirmWorkflowReconciliation(review, c, c.dbSpecJson)
    invalidateWorkflowReconciliation(review)
    expect(() => assertWorkflowReconciliation(proof, c, c.dbSpecJson)).toThrow()
    expect(() => confirmWorkflowReconciliation(review, c, c.dbSpecJson)).toThrow()
  })
  it('copies and freezes the review and detects subsequent file edits', () => {
    const c = context(), review = createWorkflowReconciliationOwner().open(c), proof = confirmWorkflowReconciliation(review, c, c.dbSpecJson)
    c.changes.push({ operation: 'delete', path: 'other' })
    expect(review.changes).toHaveLength(1)
    expect(Object.isFrozen(review.changes[0])).toBe(true)
    expect(() => assertWorkflowReconciliation(proof, c, c.dbSpecJson)).toThrow()
  })
  it.each(['{"id":"other","nodes":[]}', '{"id":"wf","id":"wf"}', '{"id":"wf","n":9007199254740993}', '{"id":"wf","n":0.10000000000000001}', 'id: wf', 'null'])('refuses unsafe or wrong identity final JSON: %s', text => {
    const c = context(), review = createWorkflowReconciliationOwner().open(c)
    expect(() => confirmWorkflowReconciliation(review, c, text)).toThrow()
  })
  it('does not require lossy DB or Git source to parse just to display them', () => {
    const c = { ...context(), dbSpecJson: 'legacy invalid raw' }
    const review = createWorkflowReconciliationOwner().open(c)
    expect(() => confirmWorkflowReconciliation(review, c, c.dbSpecJson)).toThrow()
    expect(() => confirmWorkflowReconciliation(review, c, c.candidateSpecJson)).not.toThrow()
  })
})

describe('review owner lifecycle and file evidence', () => {
  it('revokes old proofs on replacement, closing, editing, and failed replacement', () => {
    const c = context(), owner = createWorkflowReconciliationOwner()
    const first = owner.open(c), proof = confirmWorkflowReconciliation(first, c, c.dbSpecJson)
    const next = owner.open(c)
    expect(() => assertWorkflowReconciliation(proof, c, c.dbSpecJson)).toThrow()
    const proof2 = confirmWorkflowReconciliation(next, c, c.dbSpecJson)
    owner.edited()
    expect(() => assertWorkflowReconciliation(proof2, c, c.dbSpecJson)).toThrow()
    const proof3 = confirmWorkflowReconciliation(next, c, c.dbSpecJson)
    expect(() => owner.open({ ...c, expectedDraftToken: 'invalid' })).toThrow()
    expect(() => assertWorkflowReconciliation(proof3, c, c.dbSpecJson)).toThrow()
    const third = owner.open(c), proof4 = confirmWorkflowReconciliation(third, c, c.dbSpecJson)
    owner.close()
    expect(() => assertWorkflowReconciliation(proof4, c, c.dbSpecJson)).toThrow()
  })
  it('revokes earlier approval even when confirming again without an edit', () => {
    const c = context(), owner = createWorkflowReconciliationOwner(), review = owner.open(c)
    const first = confirmWorkflowReconciliation(review, c, c.dbSpecJson)
    const second = confirmWorkflowReconciliation(review, c, c.dbSpecJson)
    expect(() => assertWorkflowReconciliation(first, c, c.dbSpecJson)).toThrow()
    expect(() => assertWorkflowReconciliation(second, c, c.dbSpecJson)).not.toThrow()
  })
  it('detects modifications of previous bytes and modes, including files absent after delete', () => {
    const c = context(), review = createWorkflowReconciliationOwner().open(c)
    const proof = confirmWorkflowReconciliation(review, c, c.dbSpecJson)
    const changed = { ...c, filesBefore: c.filesBefore.map(f => f.path === 'x' ? { ...f, mode: '100644' as const } : f) }
    expect(() => assertWorkflowReconciliation(proof, changed, c.dbSpecJson)).toThrow()
    const deleted = { ...c, filesAfter: c.filesAfter.filter(f => f.path !== 'x'), changes: [{ path: 'x', operation: 'delete' as const }] }
    const deletion = createWorkflowReconciliationOwner().open(deleted)
    expect(deletion.filesBefore.find(f => f.path === 'x')?.contentBase64).toBe('eQ==')
    expect(deletion.filesAfter.find(f => f.path === 'x')).toBeUndefined()
  })
  it('rejects omitted, forged or inconsistent file changes and mismatched raw YAML', () => {
    const c = context(), owner = createWorkflowReconciliationOwner()
    expect(() => owner.open({ ...c, changes: [] })).toThrow()
    expect(() => owner.open({ ...c, changes: [{ path: 'x', operation: 'delete' }] })).toThrow()
    expect(() => owner.open({ ...c, gitYaml: 'different' })).toThrow()
    expect(() => owner.open({ ...c, filesBefore: c.filesBefore.filter(f => f.path !== 'x') })).toThrow()
    expect(() => owner.open({ ...c, filesAfter: [] })).toThrow()
  })
  it.each(['AP8=', 'YQBi', '77u/YQ=='])('refuses to confirm binary or unsafe bytes %s', contentBase64 => {
    const c = context()
    c.filesBefore.push({ path: 'binary', mode: '100644', contentBase64 })
    c.filesAfter.push({ path: 'binary', mode: '100644', contentBase64 })
    const review = createWorkflowReconciliationOwner().open(c)
    expect(() => confirmWorkflowReconciliation(review, c, c.dbSpecJson)).toThrow('不能确认已审阅')
  })
})

it('refuses to approve a binary deletion even when the candidate contains no binary file', () => {
  const c = context()
  c.filesBefore.push({ path: 'removed.bin', mode: '100755', contentBase64: 'AP8=' })
  c.changes.push({ path: 'removed.bin', operation: 'delete' })
  const review = createWorkflowReconciliationOwner().open(c)
  expect(() => confirmWorkflowReconciliation(review, c, c.dbSpecJson)).toThrow('不能确认已审阅')
})
it('keeps other owners independent when one is edited or closed', () => {
  const c = context(), a = createWorkflowReconciliationOwner(), b = createWorkflowReconciliationOwner()
  const ar = a.open(c), br = b.open(c), ap = confirmWorkflowReconciliation(ar, c, c.dbSpecJson), bp = confirmWorkflowReconciliation(br, c, c.dbSpecJson)
  a.edited(); a.close()
  expect(() => assertWorkflowReconciliation(ap, c, c.dbSpecJson)).toThrow()
  expect(() => assertWorkflowReconciliation(bp, c, c.dbSpecJson)).not.toThrow()
})

describe('invisible Unicode review safety', () => {
  it.each(['\u00ad', '\u061c', '\u034f', '\u200b', '\u{e0001}'])('rejects invisible character %j in paths and blocks file contents', char => {
    const c = context(), owner = createWorkflowReconciliationOwner()
    const hiddenPath = { path: `script${char}.sh`, mode: '100644' as const, contentBase64: 'eA==' }
    expect(() => owner.open({ ...c, filesBefore: [...c.filesBefore, hiddenPath], filesAfter: [...c.filesAfter, hiddenPath] })).toThrow('文件快照无效')
    const hiddenContent = { path: 'hidden.sh', mode: '100644' as const, contentBase64: Buffer.from(`visible${char}text`, 'utf8').toString('base64') }
    const reviewContext = { ...c, filesBefore: [...c.filesBefore, hiddenContent], filesAfter: [...c.filesAfter, hiddenContent] }
    const review = owner.open(reviewContext)
    expect(() => confirmWorkflowReconciliation(review, reviewContext, c.dbSpecJson)).toThrow('不能确认已审阅')
  })
  it.each(['\u00ad', '\u061c'])('rejects invisible character %j in displayed DB, candidate or final JSON', char => {
    const c = context(), owner = createWorkflowReconciliationOwner()
    for (const field of ['dbSpecJson', 'candidateSpecJson'] as const) {
      const changed = { ...c, [field]: `{"id":"wf","title":"x${char}y","nodes":[]}` }
      const review = owner.open(changed)
      expect(() => confirmWorkflowReconciliation(review, changed, c.dbSpecJson)).toThrow('不可安全显示')
    }
    const review = owner.open(c)
    expect(() => confirmWorkflowReconciliation(review, c, `{"id":"wf","x":"${char}"}`)).toThrow('不可安全显示')
    // Explicit JSON escapes are visible to the reviewer and not rejected by this display guard.
    expect(() => confirmWorkflowReconciliation(review, c, '{"id":"wf","x":"\\u00ad"}')).not.toThrow()
  })
})

describe('mismatch is terminal', () => {
  it.each(['valid', 'invalid'] as const)('invalidates review on observed %s mismatch even when context is restored', variant => {
    const c = context(), review = createWorkflowReconciliationOwner().open(c)
    const mismatch = { ...c, revision: variant === 'valid' ? 'session:2' : '' }
    expect(workflowReconciliationIsCurrent(review, mismatch)).toBe(false)
    expect(workflowReconciliationIsCurrent(review, c)).toBe(false)
    expect(() => confirmWorkflowReconciliation(review, c, c.dbSpecJson)).toThrow()
  })
  it('a failed assertion observes the changed context even if the spec argument also differs', () => {
    const c = context(), review = createWorkflowReconciliationOwner().open(c)
    const approval = confirmWorkflowReconciliation(review, c, c.dbSpecJson)
    expect(() => assertWorkflowReconciliation(approval, { ...c, revision: 'session:2' }, '{}')).toThrow()
    expect(() => confirmWorkflowReconciliation(review, c, c.dbSpecJson)).toThrow()
  })
})

it('allows ordinary multilingual file names and UTF-8 content', () => {
  const c = context()
  const file = { path: '脚本-é.sh', mode: '100644' as const, contentBase64: Buffer.from('echo 你好\n', 'utf8').toString('base64') }
  c.filesBefore.push(file); c.filesAfter.push(file)
  const review = createWorkflowReconciliationOwner().open(c)
  expect(() => confirmWorkflowReconciliation(review, c, c.dbSpecJson)).not.toThrow()
})
it.each(['\u00ad', '\u061c'])('blocks invisible %j in raw Git YAML', char => {
  const c = context(), text = `id: wf\ntitle: hidden${char}text\n`
  c.gitYaml = text
  c.filesBefore[0].contentBase64 = Buffer.from(text, 'utf8').toString('base64')
  c.filesAfter[0].contentBase64 = c.filesBefore[0].contentBase64
  const review = createWorkflowReconciliationOwner().open(c)
  expect(() => confirmWorkflowReconciliation(review, c, c.dbSpecJson)).toThrow('不可安全显示')
})
