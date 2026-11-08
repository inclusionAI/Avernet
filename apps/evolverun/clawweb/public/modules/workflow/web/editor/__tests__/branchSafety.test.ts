import { beforeEach, describe, expect, it } from 'vitest'
import type { WorkflowNode, WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { getResultBranches } from '../contracts'
import { branchEditError, canConnect, findNodeReferences, validateSpec } from '../graph'
import { useEditorStore } from '../store'

const state = () => useEditorStore.getState()
const node = (id: string, extra: Record<string, unknown> = {}): WorkflowNode => ({
  id, title: id, phase: 'work', executor: { type: 'done' }, ...extra,
}) as WorkflowNode
const routing = (mixed = false, complete = false) => ({ branches: [
  { branchId: 'yes', match: { approved: true }, complete, future: { zero: 0, nullable: null } },
  ...(mixed ? [{ match: {}, complete: true, future: [false, 0, null] }] : []),
] })
const router = (mixed = false, complete = false) => node('router', { onResult: routing(mixed, complete) })
const effect = (dependsOn = ['router']) => node('effect', { dependsOn, branchId: 'yes' })
const flow = (nodes: WorkflowNode[]): WorkflowSpec => ({ id: 'branch-safety', title: 'Synthetic branching', version: '1', nodes })

beforeEach(() => { useEditorStore.setState(useEditorStore.getInitialState(), true) })

function expectRejected(action: () => void) {
  const before = state()
  action()
  expect(state().actionError).toBeTruthy()
  expect(state().spec).toBe(before.spec)
  expect(state().positions).toBe(before.positions)
  expect(state().selectedNodeId).toBe(before.selectedNodeId)
  expect(state().isDirty).toBe(before.isDirty)
  expect(state().canUndo).toBe(before.canUndo)
  expect(state().canRedo).toBe(before.canRedo)
  expect(state()._past).toBe(before._past)
  expect(state()._future).toBe(before._future)
  expect(state().sessionId).toBe(before.sessionId)
}

describe('named references inside mixed named/anonymous result branches', () => {
  it('keeps raw compatibility mode without hiding the named branch reference', () => {
    const source = flow([router(true), effect()])
    const original = structuredClone(source)
    expect(getResultBranches(source.nodes[0])).toBeNull()
    expect(findNodeReferences(source, 'router')).toContain('spec.nodes[effect].branchId (yes)')
    expect(validateSpec(source)).toEqual([])
    expect(source).toEqual(original)
  })

  it.each([true, false])('accepts its real named branch connection with complete=%s', complete => {
    const source = flow([router(true, complete), node('effect')])
    expect(canConnect(source, 'router', 'effect', 'yes')).toBeNull()
    state().loadSpec(source)
    state().addEdge('router', 'effect', 'yes')
    expect(state().actionError).toBeNull()
    expect(state().spec!.nodes[1]).toEqual(effect())
    expect(state().spec!.nodes[0]).toEqual(source.nodes[0])
    expect(state().validationErrors).toEqual([])
  })

  it.each(['delete-source', 'remove-branch', 'rename-branch', 'rename-membership'] as const)(
    'rejects %s without silently detaching the effect', operation => {
      const source = flow([router(true), effect()])
      state().loadSpec(source)
      state().selectNode('effect')
      expectRejected(() => {
        if (operation === 'delete-source') state().removeNode('router')
        else if (operation === 'rename-membership') state().updateNode('effect', { branchId: 'other' })
        else {
          const changed = routing(true)
          if (operation === 'remove-branch') changed.branches.shift()
          else changed.branches[0].branchId = 'other'
          state().updateNode('router', node('router', { onResult: changed }))
        }
      })
      expect(state().actionError).toContain('effect')
      expect(state().spec).toEqual(source)
    },
  )

  it.each(['remove', 'rename'] as const)('protects a transitive mixed-source branch from %s', operation => {
    const before = flow([router(true), node('relay', { dependsOn: ['router'] }), effect(['relay'])])
    const after = structuredClone(before)
    const changed = routing(true)
    if (operation === 'remove') changed.branches.shift()
    else changed.branches[0].branchId = 'other'
    after.nodes[0] = node('router', { onResult: changed })
    expect(branchEditError(before, after)).toContain('effect')
    state().loadSpec(before)
    expectRejected(() => state().updateNode('router', after.nodes[0]))
  })

  it('does not let adding an anonymous fallback hide duplicate named IDs elsewhere', () => {
    const source = flow([router(true), node('another', { onResult: routing() })])
    expect(validateSpec(source)).toContainEqual(expect.objectContaining({ type: 'branch-conflict', message: expect.stringContaining('yes') }))
  })
})

describe('preservation of the final effective branch-source path', () => {
  it.each([true, false])('refuses the last direct source edge with complete=%s', complete => {
    state().loadSpec(flow([router(false, complete), effect()]))
    expectRejected(() => state().removeEdge('router', 'effect'))
    expect(state().actionError).toContain('yes')
    expect(state().actionError).toContain('effect')
  })

  it.each([
    ['router', 'relay'], ['relay', 'effect'],
  ])('refuses cutting the transitive path at %s → %s', (source, target) => {
    state().loadSpec(flow([router(true), node('relay', { dependsOn: ['router'] }), effect(['relay'])]))
    expectRejected(() => state().removeEdge(source, target))
    expect(state().actionError).toContain('effect')
  })

  it.each(['patch-direct', 'patch-relay', 'delete-relay'] as const)('protects against bypass through %s', operation => {
    state().loadSpec(flow([router(true), node('relay', { dependsOn: ['router'] }), effect(['relay'])]))
    expectRejected(() => {
      if (operation === 'patch-direct') state().updateNode('effect', { dependsOn: [] })
      else if (operation === 'patch-relay') state().updateNode('relay', { dependsOn: [] })
      else state().removeNode('relay')
    })
    expect(state().actionError).toContain('effect')
  })

  it('reports imported named branch membership without any origin path', () => {
    const source = flow([router(true), effect([])])
    state().loadSpec(source)
    expect(state().spec).toEqual(source)
    expect(state().validationErrors).toContainEqual(expect.objectContaining({
      type: 'branch-conflict', nodeId: 'effect', message: expect.stringContaining('yes'),
    }))
  })

  it.each(['direct', 'relay-edge', 'delete-relay'] as const)('allows %s removal when another effective path remains and supports undo/redo', operation => {
    const source = flow([
      router(true, true), node('left', { dependsOn: ['router'] }), node('right', { dependsOn: ['router'] }),
      effect(['router', 'left', 'right']),
    ])
    state().loadSpec(source)
    if (operation === 'direct') state().removeEdge('router', 'effect')
    else if (operation === 'relay-edge') state().removeEdge('router', 'left')
    else state().removeNode('left')
    expect(state().actionError).toBeNull()
    expect(state().validationErrors).toEqual([])
    expect(state().isDirty).toBe(true)
    expect(state().spec!.nodes.find(item => item.id === 'effect')!.branchId).toBe('yes')
    const changed = structuredClone(state().spec)
    state().undo()
    expect(state().spec).toEqual(source)
    expect(state().isDirty).toBe(false)
    state().redo()
    expect(state().spec).toEqual(changed)
    expect(state().validationErrors).toEqual([])
  })

  it('allows one of two transitive paths to be removed but refuses removing the remaining one', () => {
    state().loadSpec(flow([
      router(), node('left', { dependsOn: ['router'] }), node('right', { dependsOn: ['router'] }), effect(['left', 'right']),
    ]))
    state().removeEdge('left', 'effect')
    expect(state().actionError).toBeNull()
    expect(state().spec!.nodes.at(-1)!.dependsOn).toEqual(['right'])
    expectRejected(() => state().removeEdge('right', 'effect'))
  })

  it('checks every branch member, not only a descendant that still has another path', () => {
    state().loadSpec(flow([
      router(), node('left', { dependsOn: ['router'], branchId: 'yes' }),
      node('right', { dependsOn: ['router'] }), effect(['left', 'right']),
    ]))
    expectRejected(() => state().removeEdge('router', 'left'))
    expect(state().actionError).toContain('left')
  })

  it('does not count a path through a different branch-producing node as inherited ancestry', () => {
    state().loadSpec(flow([
      router(), node('left', { dependsOn: ['router'] }),
      node('other-router', { dependsOn: ['router'], onResult: { branches: [{ branchId: 'no', match: {} }] } }),
      node('right', { dependsOn: ['other-router'] }), effect(['left', 'right']),
    ]))
    expectRejected(() => state().removeEdge('left', 'effect'))
    expect(state().actionError).toContain('effect')
  })

  it('checks unreachable branch paths in cyclic imported input without recursive traversal', () => {
    const source = flow([router(), node('relay', { dependsOn: ['effect'] }), effect(['relay'])])
    expect(validateSpec(source).map(error => error.type)).toEqual(expect.arrayContaining(['cycle', 'branch-conflict']))
    state().loadSpec(source)
    state().updateNode('relay', { title: 'Inspect invalid imported graph' })
    expect(state().actionError).toBeNull()
  })
})
