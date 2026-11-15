import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ActiveHistoryMismatchReview from '../ActiveHistoryMismatchReview'

const evidence = {
  workflowId: 'flow',
  deployNumber: 21,
  gitPath: 'workflow.yaml',
  databaseText: JSON.stringify({ id: 'flow', title: 'DB record' }),
  gitYaml: 'id: flow\ntitle: Git tag\n',
}

afterEach(cleanup)

describe('ActiveHistoryMismatchReview', () => {
  it('reuses the editor comparison notice, shows a diff, and lets the user choose Git', async () => {
    const onChoose = vi.fn()
    render(<ActiveHistoryMismatchReview evidence={evidence} deployNumber={21} selected="database" onChoose={onChoose} />)
    expect(screen.getByText(/生效 #21的数据库发布记录与 Git 文件存在差异/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '查看差异' }))
    const dialog = screen.getByRole('dialog', { name: '对比生效数据库记录和 Git 工作流' })
    expect(within(dialog).getByRole('region', { name: /数据库发布记录 #21内容/ })).toHaveTextContent('DB record')
    expect(within(dialog).getByRole('region', { name: 'Git 原始 YAML内容' })).toHaveTextContent('Git tag')
    fireEvent.click(within(dialog).getByRole('radio', { name: /^使用 Git YAML/ }))
    fireEvent.click(within(dialog).getByRole('button', { name: '使用 Git YAML' }))
    expect(onChoose).toHaveBeenCalledWith('git')
  })

  it('keeps the warning when the raw text is unavailable', () => {
    const onChoose = vi.fn()
    const incomplete = { ...evidence, databaseText: null }
    render(<ActiveHistoryMismatchReview evidence={incomplete} deployNumber={21} selected="database" onChoose={onChoose} />)
    expect(screen.getByText(/两侧原文暂不可完整读取/)).toBeVisible()
    expect(screen.queryByRole('button', { name: '查看差异' })).not.toBeInTheDocument()
    expect(onChoose).not.toHaveBeenCalled()
  })
})
