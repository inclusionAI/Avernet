import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import ScriptFilesTab from '../ScriptFilesTab'
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
it('clearly disables the public write path and never issues PUT, retaining local edits', async () => {
  vi.mocked(fetchJson).mockResolvedValueOnce({ workflowId: 'disabled-workflow', packId: 'pack', baseRef: 'origin/wf/disabled-workflow', baseCommit: 'a'.repeat(40), draftHead: 'a'.repeat(40), versionFiles: [{ path: 'workflow.yaml', contentBase64: 'b2xk', mode: '100644' }] })
  render(<ScriptFilesTab workflowId="disabled-workflow" />)
  fireEvent.click(await screen.findByRole('button', { name: 'workflow.yaml' }))
  fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: 'unsaved candidate' } })
  expect(screen.getByText(/写入尚未启用/)).toHaveTextContent('不能保存或部署')
  expect(screen.getByRole('button', { name: '保存到草稿' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '保存到草稿' }))
  expect(fetchJson).toHaveBeenCalledTimes(1)
  expect(screen.getByLabelText('文件内容')).toHaveValue('unsaved candidate')
})
