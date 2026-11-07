import { beforeEach, expect, it, vi } from 'vitest'
import { useEditorStore } from '../store'
import { beginWorkflowTransition, commitWorkflowTransition, prepareWorkflowTransition } from '../workflowTransition'

vi.mock('../packVersionSession', () => ({ hasAnyPackVersionRisk: () => false, hasAnyPackVersionUpload: () => false, packVersionsRevision: () => 0 }))
const source = { id: 'flow', title: 'Active source', version: '1', nodes: [] }
const database = { ...source, title: 'Database baseline' }
const state = () => useEditorStore.getState()
beforeEach(() => { state().reset({ force: true }) })
it('retains an independently cloned DB baseline on load and explicit transition', () => {
  state().loadSpec(source, database)
  expect(state().spec).toEqual(source); expect(state()._savedSpec).toEqual(source)
  expect(state()._databaseSpec).toEqual(database); expect(state()._databaseSpec).not.toBe(database)
  expect(state().isDirty).toBe(false)
  const approval = beginWorkflowTransition()!
  expect(prepareWorkflowTransition(approval, source, source.id)).toEqual(source)
  expect(commitWorkflowTransition(approval, source, source.id, false, database)).toBe(true)
  expect(state()._databaseSpec).toEqual(database)
})
it('distinguishes an absent DB draft from an omitted backwards-compatible baseline', () => {
  state().loadSpec(source, null, 7)
  expect(state()._databaseSpec).toBeNull(); expect(state()._activeDeployNumber).toBe(7); expect(state()._savedSpec).toEqual(source)
  expect(commitWorkflowTransition(beginWorkflowTransition()!, source, source.id, false, null, 7)).toBe(true)
  expect(state()._databaseSpec).toBeNull(); expect(state()._activeDeployNumber).toBe(7)
  state().loadSpec(source)
  expect(state()._databaseSpec).toEqual(source)
})
it.each([{ ...database, id: 'wrong' }, { ...database, nodes: null }])('rejects malformed or mismatched DB baselines without replacing drafts', invalid => {
  state().loadSpec(source)
  const before = state()
  expect(state().replaceSession(source, before, false, invalid)).toBe(false)
  expect(state()).toBe(before)
  state().loadSpec(source, invalid)
  expect(state()._databaseSpec).toEqual(source)
  expect(state().sessionId).toBe(before.sessionId)
})
it('updates only the DB/saved baselines on completion and never restores them through undo', () => {
  state().loadSpec(source, null, 7)
  state().updateSpecField('title', 'Save this')
  const request = structuredClone(state().spec!)
  state().updateSpecField('title', 'Later edit')
  expect(state().completeSave(request, request, state().sessionId)).toBe(true)
  expect(state().spec!.title).toBe('Later edit'); expect(state().isDirty).toBe(true)
  expect(state()._databaseSpec).toEqual(request)
  state().undo(); expect(state()._databaseSpec).toEqual(request); expect(state().isDirty).toBe(false)
  request.title = 'Mutated caller'
  expect(state()._databaseSpec!.title).toBe('Save this')
  state().markClean(); expect(state()._databaseSpec).toEqual(state().spec)
})
it('clears DB baselines for new sessions and resets', () => {
  state().loadSpec(source)
  expect(commitWorkflowTransition(beginWorkflowTransition()!, source, source.id, true, database)).toBe(true)
  expect(state()._databaseSpec).toBeNull(); expect(state()._savedSpec).toBeNull()
  state().reset({ force: true }); state().createNew('new', 'New')
  expect(state()._databaseSpec).toBeNull()
  state().reset({ force: true }); expect(state()._databaseSpec).toBeNull()
})

it.each(['default', 'explicit', 'absent'] as const)('loads and clones historical null versions and preserves them across replacement: %s baseline', kind => {
  const legacy = { ...source, version: null, custom: { preserved: false } }
  const db = kind === 'absent' ? null : { ...legacy, title: 'Historical draft', custom: { preserved: false } }
  if (kind === 'default') state().loadSpec(legacy)
  else state().loadSpec(legacy, db, kind === 'absent' ? 7 : undefined)
  expect(state().actionError).toBeNull()
  expect(state().spec).toEqual(legacy)
  expect(state()._savedSpec).toEqual(legacy)
  expect(state()._databaseSpec).toEqual(kind === 'default' ? legacy : db)
  legacy.custom.preserved = true
  if (db) db.custom.preserved = true
  expect(state().spec!.custom).toEqual({ preserved: false })
  if (kind !== 'absent') expect(state()._databaseSpec!.custom).toEqual({ preserved: false })
  state().updateSpecField('title', 'Edited historical source')
  expect(state().actionError).toBeNull(); expect(state().spec!.version).toBeNull()
  expect(state().isDirty).toBe(true)
  const before = state()
  expect(state().replaceSession({ ...source, version: null }, before, false)).toBe(true)
  expect(state().spec?.version).toBeNull(); expect(state()._databaseSpec?.version).toBeNull()
})
it('accepts a legacy null-version DB baseline without applying session-source version rules to loadSpec', () => {
  const legacyDb = { ...database, version: null }
  state().loadSpec(source, legacyDb)
  expect(state().spec).toEqual(source); expect(state()._databaseSpec).toEqual(legacyDb)
  expect(state().actionError).toBeNull()
})
