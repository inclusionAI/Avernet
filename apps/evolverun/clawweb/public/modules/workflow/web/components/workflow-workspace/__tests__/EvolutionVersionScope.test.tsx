import { expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { render, repairApi, repairItem, repairPage } from './evolution-issue-fixture'
import EvolutionTab from '../EvolutionTab'
import { workflowSnapshotKey } from '@avernet/clawweb-shared/server/contracts/workflow-snapshot'

it('submits explicitly selected historical originals against one visible current target', async () => {
  const active = { kind: 'deployment' as const, workflowId: 'wf-1', workflowVersion: 40, deployNumber: 35, configDigest: 'a'.repeat(64) }
  const old = { ...repairItem('historical', '历史约束'), state: 'verified', selectionDigest: 'd'.repeat(64), context: { signature: 'timeout:fetch-data', sourceSnapshot: { status: 'resolved', snapshot: { ...active, workflowVersion: 39, deployNumber: 34 } } } }
  repairApi.candidates.mockImplementation((_wf, query) => Promise.resolve({ ...repairPage({ items: query?.versionScope === 'all' ? [old] as never : [] }),
    activeSnapshot: { status: 'resolved', snapshot: active }, versionScope: query?.versionScope ?? 'current' }))
  repairApi.selection.mockResolvedValue({ items: [old], missing: [], inputDigest: 'c'.repeat(64), canEdit: true, activeSnapshot: { status: 'resolved', snapshot: active } })
  const user = userEvent.setup()
  render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
  await user.click(await screen.findByRole('button', { name: '查找全部建议' }))
  await user.click(await screen.findByRole('checkbox', { name: '选择 历史约束' }))
  await user.click(screen.getByRole('button', { name: '生成优化草稿（1）' }))
  const submit = await screen.findByRole('button', { name: '生成 Pack 草稿' })
  await waitFor(() => expect(submit).toBeEnabled())
  await user.click(submit)
  await waitFor(() => expect(repairApi.create).toHaveBeenCalledTimes(1))
  expect(repairApi.create.mock.calls[0][0]).toMatchObject({ itemIds: ['historical'], historicalItemIds: ['historical'], targetSnapshotKey: workflowSnapshotKey(active) })
  expect(repairApi.candidates).toHaveBeenCalledWith('wf-1', expect.objectContaining({ versionScope: 'current' }))
})
