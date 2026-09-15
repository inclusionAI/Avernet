// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { EvolveTask } from '../../api/client'
import SkillTaskRuntimePanel from '../SkillTaskRuntimePanel'

const api = vi.hoisted(() => ({ evolve: { getTaskSkillDiff: vi.fn() } }))
vi.mock('../../api/client', () => ({ api }))

function task(artifact?: { ref: string; sha256: string }): EvolveTask {
  return {
    task_id: 'TASK-1', status: 'running', interactions: [],
    config: { targetSkill: { assetId: 'SKILL-1', name: 'Example', candidate: artifact ? { artifact } : undefined } },
  } as unknown as EvolveTask
}

function panel(value: EvolveTask) {
  return <SkillTaskRuntimePanel task={value} canOperate={false} onUpdated={async () => {}} />
}

const readyDiff = { files: [{ path: 'SKILL.md', change: 'modified', before: 'shared\nFrozen baseline', after: 'shared\nReady candidate' }] }

beforeEach(() => { vi.stubGlobal('React', React); vi.resetAllMocks() })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Skill candidate diff polling', () => {
  it('does not request a missing candidate and loads the diff when polling supplies it', async () => {
    api.evolve.getTaskSkillDiff.mockResolvedValueOnce(readyDiff)
    const view = render(panel(task()))
    expect(screen.getByText('候选版本尚未生成。')).toBeTruthy()
    expect(api.evolve.getTaskSkillDiff).not.toHaveBeenCalled()

    view.rerender(panel(task({ ref: 'candidate/package.zip', sha256: 'ready-hash' })))

    const summary = await screen.findByText('Skill 候选版本')
    const candidate = summary.closest('details')!
    expect(candidate.open).toBe(false)
    expect(screen.queryByText('完整对比')).toBeNull()
    fireEvent.click(summary)
    expect(candidate.open).toBe(true)
    expect(candidate.textContent).toContain('-Frozen baseline')
    expect(candidate.textContent).toContain('+Ready candidate')
    expect(screen.queryByText('任务开始时')).toBeNull()
    expect(screen.queryByText('候选尚未生成')).toBeNull()
    expect(api.evolve.getTaskSkillDiff).toHaveBeenCalledTimes(1)
  })

  it('shows a completed task without an applicable candidate without issuing a 409 request', () => {
    render(panel({ ...task(), status: 'completed' }))
    expect(screen.getByText('任务已结束，未产生可应用的候选版本。')).toBeTruthy()
    expect(api.evolve.getTaskSkillDiff).not.toHaveBeenCalled()
  })

  it('preserves genuine diff failures once an artifact exists', async () => {
    api.evolve.getTaskSkillDiff.mockRejectedValue(new Error('Historical snapshot unavailable'))
    render(panel(task({ ref: 'candidate.zip', sha256: 'sha' })))
    expect(await screen.findByText('Historical snapshot unavailable')).toBeTruthy()
  })

  it.each(['reject', 'resolve'] as const)('ignores a stale request that finishes with %s after the current candidate', async (outcome) => {
    let resolveOld!: (value: typeof readyDiff) => void
    let rejectOld!: (reason: Error) => void
    const old = new Promise<typeof readyDiff>((resolve, reject) => { resolveOld = resolve; rejectOld = reject })
    api.evolve.getTaskSkillDiff.mockReturnValueOnce(old).mockResolvedValueOnce(readyDiff)
    const view = render(panel(task({ ref: 'old.zip', sha256: 'old' })))
    view.rerender(panel(task({ ref: 'candidate/package.zip', sha256: 'ready-hash' })))
    await screen.findByText('Ready candidate')

    await act(async () => {
      if (outcome === 'reject') rejectOld(new Error('Stale failure'))
      else resolveOld({ files: [{ ...readyDiff.files[0], after: 'Stale candidate' }] })
      await old.catch(() => {})
    })

    expect(screen.getByText('Ready candidate')).toBeTruthy()
    expect(screen.queryByText('Stale candidate')).toBeNull()
    expect(screen.queryByText('Stale failure')).toBeNull()
  })

  it('does not refetch for unchanged polling data but refreshes when the artifact hash changes', async () => {
    api.evolve.getTaskSkillDiff.mockResolvedValueOnce(readyDiff)
      .mockResolvedValueOnce({ files: [{ ...readyDiff.files[0], after: 'New candidate' }] })
    const artifact = { ref: 'candidate/package.zip', sha256: 'first-hash' }
    const view = render(panel(task(artifact)))
    await screen.findByText('Ready candidate')
    view.rerender(panel(task({ ...artifact })))
    expect(api.evolve.getTaskSkillDiff).toHaveBeenCalledTimes(1)

    view.rerender(panel(task({ ...artifact, sha256: 'second-hash' })))
    await screen.findByText('New candidate')
    expect(screen.queryByText('Ready candidate')).toBeNull()
    expect(api.evolve.getTaskSkillDiff).toHaveBeenCalledTimes(2)
  })

  it('clears the previous task diff while loading another task', async () => {
    api.evolve.getTaskSkillDiff.mockResolvedValueOnce(readyDiff).mockReturnValueOnce(new Promise(() => {}))
    const view = render(panel(task({ ref: 'first.zip', sha256: 'first' })))
    await screen.findByText('Ready candidate')
    view.rerender(panel({ ...task({ ref: 'second.zip', sha256: 'second' }), task_id: 'TASK-2' }))
    expect(screen.queryByText('Ready candidate')).toBeNull()
    expect(screen.queryByText('Frozen baseline')).toBeNull()
    expect(api.evolve.getTaskSkillDiff).toHaveBeenLastCalledWith('TASK-2')
  })
})
