import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import Sidebar from '../Sidebar'

function renderSidebar(isAdmin: boolean) {
  render(
    <MemoryRouter>
      <Sidebar
        activeView="diagnosis"
        onViewChange={vi.fn()}
        isAdmin={isAdmin}
        hasWorkflow
        counts={{ diagnosis: 7, remedies: 1 }}
      />
    </MemoryRouter>,
  )
}

describe('task escort workspace sidebar', () => {
  it('puts the administrator dashboard and workflow views in one navigation', () => {
    renderSidebar(true)

    expect(screen.getByText('TASK GUARD')).toBeInTheDocument()
    expect(screen.getByText('分析与改进')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /数据大盘/ })).toBeInTheDocument()
    const activeItem = screen.getByRole('button', { name: /问题与优化.*7/ })
    expect(activeItem).toHaveAttribute('aria-current', 'page')
    expect(activeItem.querySelector('.bg-blue-600')).toBeInTheDocument()
    expect(within(activeItem).getByText('7')).toHaveClass('rounded-full')
    expect(screen.queryByRole('button', { name: /优化建议/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /可复用经验.*1/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /编辑器/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /管理设置/ })).toBeInTheDocument()
  })

  it('does not expose the global dashboard entry to a personal user', () => {
    renderSidebar(false)

    expect(screen.queryByRole('button', { name: /数据大盘/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /问题与优化.*7/ })).toBeInTheDocument()
  })

  it('uses one consistent SVG icon system for workspace modules', () => {
    renderSidebar(true)

    for (const name of ['数据大盘', '运行概览', '问题与优化 7', '可复用经验 1', '编辑器', '管理设置']) {
      const icon = screen.getByRole('button', { name }).querySelector('svg')
      expect(icon).toBeInTheDocument()
      expect(icon).toHaveAttribute('aria-hidden', 'true')
      expect(icon).toHaveClass('h-4', 'w-4')
    }
  })

  it('keeps workflow switching out of the navigation sidebar', () => {
    renderSidebar(true)

    expect(screen.queryByText('当前工作流')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /技术调研.*tech-research/ })).not.toBeInTheDocument()
  })
})
