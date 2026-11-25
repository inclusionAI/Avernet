import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import NodePalette from '../NodePalette'
import { useEditorStore } from '../../editor/store'
import { NODE_CATALOG } from '../../editor/nodeCatalog'
import { NODE_DRAG_TYPE } from '../editor/CanvasPresentation'

describe('NodePalette', () => {
  beforeEach(() => {
    useEditorStore.setState(useEditorStore.getInitialState(), true)
    useEditorStore.getState().createNew('palette-test', '节点库测试')
  })
  afterEach(() => { cleanup(); vi.restoreAllMocks() })

  it('shows six common business labels and reveals advanced types explicitly', async () => {
    render(<NodePalette />)
    const common = within(screen.getByRole('region', { name: '常用节点' }))
    expect(common.getAllByRole('button')).toHaveLength(6)
    for (const item of NODE_CATALOG.filter((entry) => entry.group === 'common')) {
      expect(common.getByRole('button', { name: `添加${item.label}` })).toBeVisible()
    }
    expect(screen.queryByRole('button', { name: '添加子代理' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /更多节点/ }))
    expect(screen.getByRole('button', { name: /更多节点/ })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('button', { name: '添加子代理' })).toBeVisible()
  })

  it('searches Chinese labels, raw types and descriptions, including collapsed advanced nodes', async () => {
    render(<NodePalette />)
    const input = screen.getByRole('textbox', { name: '搜索节点类型' })
    await userEvent.type(input, '脚本')
    expect(screen.getByRole('button', { name: '添加脚本执行' })).toBeVisible()
    expect(screen.queryByRole('button', { name: '添加AI 处理' })).not.toBeInTheDocument()
    await userEvent.clear(input)
    await userEvent.type(input, 'SUBAGENT')
    expect(screen.getByRole('button', { name: '添加子代理' })).toBeVisible()
    await userEvent.clear(input)
    await userEvent.type(input, '通过规则')
    expect(screen.getByRole('button', { name: '添加审批' })).toBeVisible()
    await userEvent.clear(input)
    await userEvent.type(input, 'not-a-type')
    expect(screen.getByRole('status')).toHaveTextContent('没有匹配的节点类型')
  })

  it('adds and selects legitimate unique nodes using keyboard and click without fake config', async () => {
    render(<NodePalette />)
    const button = screen.getByRole('button', { name: '添加脚本执行' })
    button.focus()
    await userEvent.keyboard('{Enter}')
    await userEvent.click(button)
    const { spec, selectedNodeId } = useEditorStore.getState()
    expect(spec!.nodes).toHaveLength(2)
    expect(new Set(spec!.nodes.map((node) => node.id)).size).toBe(2)
    for (const node of spec!.nodes) {
      expect(node.phase).toMatch(/^P\d+$/)
      expect(node.executor).toEqual({ type: 'cli-script', command: '' })
      expect(node).not.toHaveProperty('_x')
      expect(node).not.toHaveProperty('_y')
    }
    expect(selectedNodeId).toBe(spec!.nodes[1].id)
  })

  it('keeps the optional onAdd callback as an override instead of double-adding', async () => {
    const onAdd = vi.fn()
    render(<NodePalette onAdd={onAdd} />)
    await userEvent.click(screen.getByRole('button', { name: '添加工具调用' }))
    expect(onAdd).toHaveBeenCalledExactlyOnceWith('mcp-call')
    expect(useEditorStore.getState().spec!.nodes).toHaveLength(0)
  })

  it('publishes the drag MIME/type and does not add until a canvas drop', () => {
    render(<NodePalette />)
    const dataTransfer = { setData: vi.fn(), effectAllowed: '' }
    fireEvent.dragStart(screen.getByRole('button', { name: '添加人工输入' }), { dataTransfer })
    expect(dataTransfer.setData).toHaveBeenCalledWith(NODE_DRAG_TYPE, 'human')
    expect(dataTransfer.effectAllowed).toBe('copy')
    expect(useEditorStore.getState().spec!.nodes).toHaveLength(0)
  })

  it('does not replace selection or discard drafts when adding is rejected', async () => {
    useEditorStore.getState().setPendingEdit('yaml', '未应用 YAML')
    render(<NodePalette />)
    await userEvent.click(screen.getByRole('button', { name: '添加完成' }))
    expect(useEditorStore.getState().spec!.nodes).toHaveLength(0)
    expect(useEditorStore.getState().selectedNodeId).toBeNull()
    expect(useEditorStore.getState().pendingEdits).toEqual({ yaml: '未应用 YAML' })
    expect(useEditorStore.getState().actionError).toMatch(/未应用/)
  })

  it('disables adding without a workflow, but allows an explicit host callback', () => {
    useEditorStore.setState(useEditorStore.getInitialState(), true)
    const { rerender } = render(<NodePalette />)
    expect(screen.getByRole('button', { name: '添加完成' })).toBeDisabled()
    rerender(<NodePalette onAdd={vi.fn()} />)
    expect(screen.getByRole('button', { name: '添加完成' })).toBeEnabled()
  })
})
