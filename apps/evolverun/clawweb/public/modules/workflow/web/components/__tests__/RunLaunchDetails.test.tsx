import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import RunLaunchDetails from '../RunLaunchDetails'

type Run = Parameters<typeof RunLaunchDetails>[0]['run']

function LocationProbe() {
  const location = useLocation()
  return <output aria-label="当前路径">{location.pathname}{location.search}</output>
}

function renderDetails(fields: Partial<Run> = {}, options: { path?: string; basename?: string } = {}) {
  return render(
    <MemoryRouter basename={options.basename} initialEntries={[options.path ?? '/runs/current']}>
      <Routes>
        <Route path="/runs/:flowId" element={<RunLaunchDetails run={{ flow_id: 'current', ...fields }} />} />
      </Routes>
      <LocationProbe />
    </MemoryRouter>,
  )
}

describe('RunLaunchDetails', () => {
  it('shows a two-zone launch/lineage view with Chinese labels and exact storage fields', () => {
    renderDetails({
      input_json: JSON.stringify({ message: '请整理用户反馈', params: { requested: 'retry' } }),
      params_json: JSON.stringify({ requested: 'original' }), parent_flow_id: 'parent-run', root_flow_id: 'root-run',
    })
    const input = within(screen.getByRole('region', { name: '用户启动输入' }))
    expect(input.getByRole('region', { name: '输入文本内容' })).toHaveTextContent('请整理用户反馈')
    expect(input.getByRole('region', { name: '原始启动参数内容' })).toHaveTextContent('original')
    expect(input.queryByText(/retry/)).not.toBeInTheDocument()
    expect(input.getByText('flow_runs.input_json · message')).toBeInTheDocument()
    expect(input.getByText('flow_runs.params_json')).toBeInTheDocument()
    const lineage = within(screen.getByRole('complementary', { name: '运行链路' }))
    expect(lineage.getByText('flow_runs.parent_flow_id')).toBeInTheDocument()
    expect(lineage.getByText('flow_runs.root_flow_id')).toBeInTheDocument()
    expect(lineage.getByRole('link', { name: '查看父运行 parent-run' })).toHaveAttribute('href', '/runs/parent-run')
    expect(lineage.getByRole('link', { name: '查看根运行 root-run' })).toHaveAttribute('href', '/runs/root-run')
    expect(screen.queryAllByRole('term')).toHaveLength(0)
  })

  it('uses semantic surface, text, border and link tokens for the launch and lineage panels', () => {
    renderDetails({ input_json: '{"message":"launch text"}', params_json: '{"mode":"original"}', parent_flow_id: 'parent' })
    expect(screen.getByRole('region', { name: '用户启动输入' })).toHaveClass('border-border', 'bg-muted/30')
    expect(screen.getByRole('complementary', { name: '运行链路' })).toHaveClass('border-border', 'bg-card')
    expect(screen.getByRole('heading', { name: '用户启动输入' })).toHaveClass('text-foreground')
    expect(screen.getByRole('region', { name: '输入文本内容' })).toHaveClass('border-border', 'bg-card', 'text-foreground')
    expect(screen.getByRole('link', { name: '查看父运行 parent' })).toHaveClass('text-primary', 'focus-visible:ring-ring')
    expect(screen.getByLabelText('根运行未记录')).toHaveClass('text-muted-foreground')
  })

  it('labels command fallback as a stored command, not original user prose', () => {
    renderDetails({ input_json: JSON.stringify({ command: '/workflow start example', digest: 'hidden-digest', userId: 'hidden-user' }) })
    expect(screen.getByRole('region', { name: '已存储命令内容' })).toHaveTextContent('/workflow start example')
    expect(screen.getByText('flow_runs.input_json · command')).toBeInTheDocument()
    expect(screen.queryByText(/hidden-/)).not.toBeInTheDocument()
  })

  it.each([{}, { params_json: null, input_json: null, parent_flow_id: null, root_flow_id: null }, { parent_flow_id: '', root_flow_id: '  ' }])('shows missing launch data and lineage without inferred IDs', fields => {
    renderDetails(fields)
    expect(screen.getAllByText('未记录')).toHaveLength(2)
    expect(screen.getByLabelText('父运行未记录')).toHaveTextContent('—')
    expect(screen.getByLabelText('根运行未记录')).toHaveTextContent('—')
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('does not infer the missing root from an existing parent', () => {
    renderDetails({ parent_flow_id: 'only-parent' })
    expect(screen.getAllByRole('link')).toHaveLength(1)
    expect(screen.getByLabelText('根运行未记录')).toHaveTextContent('—')
  })

  it('handles malformed JSON with clear notices and no raw metadata dump', () => {
    renderDetails({ input_json: '{"digest":"not-user-text",', params_json: '{broken' })
    expect(screen.getByText('输入记录格式异常，无法提取文本')).toBeInTheDocument()
    expect(screen.getByText('启动参数记录格式异常，无法确认原始输入')).toBeInTheDocument()
    expect(screen.queryByText(/not-user-text/)).not.toBeInTheDocument()
  })

  it('does not render execution state or fall back to retry params', () => {
    const { container } = renderDetails({
      params_json: JSON.stringify({ _clawmind_state: { authorization: 'never-leak-credential' } }),
      input_json: JSON.stringify({ params: { authorization: 'retry-credential' }, digest: 'private-digest' }),
    })
    expect(screen.getByText('仅有执行状态，未记录原始启动参数')).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/never-leak|retry-credential|private-digest|_clawmind_state/)
  })

  it('keeps run IDs literal and encoded, mounted under the router basename with workspace provenance only', async () => {
    const user = userEvent.setup()
    const parent = 'parent/中文 ?#%&=value'
    const root = 'javascript:alert(1)//host'
    renderDetails({ parent_flow_id: parent, root_flow_id: root }, {
      basename: '/task-escort', path: '/task-escort/runs/current?from=workspace&analysisId=old&untrusted=1',
    })
    const link = screen.getByRole('link', { name: `查看父运行 ${parent}` })
    expect(link).toHaveAttribute('href', `/task-escort/runs/${encodeURIComponent(parent)}?from=workspace`)
    expect(link).toHaveTextContent(parent)
    expect(link).toHaveClass('font-mono', '[overflow-wrap:anywhere]')
    expect(screen.getByRole('link', { name: `查看根运行 ${root}` })).toHaveAttribute('href', `/task-escort/runs/${encodeURIComponent(root)}?from=workspace`)
    await user.click(link)
    expect(screen.getByLabelText('当前路径')).toHaveTextContent(`/runs/${encodeURIComponent(parent)}?from=workspace`)
  })

  it('does not add workspace provenance for unrelated from values', () => {
    renderDetails({ parent_flow_id: 'parent' }, { path: '/runs/current?from=external&analysisId=old' })
    expect(screen.getByRole('link')).toHaveAttribute('href', '/runs/parent')
  })

  it('keeps a very long ID fully visible without truncation', () => {
    const id = `parent-${'x'.repeat(300)}`
    renderDetails({ parent_flow_id: id })
    const link = screen.getByRole('link')
    expect(link.textContent).toBe(id)
    expect(link.className).not.toMatch(/truncate|line-clamp/)
    expect(link).toHaveClass('[overflow-wrap:anywhere]')
  })

  it('supports keyboard expansion/collapse of bounded, scrollable long input and JSON', async () => {
    const user = userEvent.setup()
    const message = '超长输入'.repeat(1500)
    const params = { longValue: 'json-value'.repeat(400), rows: Array.from({ length: 10 }, (_, i) => i) }
    renderDetails({ input_json: JSON.stringify({ message }), params_json: JSON.stringify(params) })
    for (const label of ['输入文本', '原始启动参数']) {
      const content = screen.getByRole('region', { name: `${label}内容` })
      const button = screen.getByRole('button', { name: `展开${label}` })
      expect(content).toHaveClass('overflow-auto', 'max-h-28', 'whitespace-pre-wrap', '[overflow-wrap:anywhere]')
      expect(content).toHaveAttribute('tabindex', '0')
      expect(button).toHaveAttribute('aria-controls', content.id)
      expect(button).toHaveAttribute('aria-expanded', 'false')
      button.focus()
      await user.keyboard('{Enter}')
      expect(button).toHaveAttribute('aria-expanded', 'true')
      expect(content).toHaveClass('max-h-96', 'overflow-auto')
      await user.keyboard(' ')
      expect(button).toHaveAttribute('aria-expanded', 'false')
      expect(content).toHaveClass('max-h-28')
    }
    expect(screen.getByRole('region', { name: '输入文本内容' }).textContent).toBe(message)
    expect(JSON.parse(screen.getByRole('region', { name: '原始启动参数内容' }).textContent!)).toEqual(params)
  })

  it('renders HTML-looking user text literally without injecting elements', () => {
    const text = '<img src=x onerror="alert(1)"><script>alert(1)</script>'
    const { container } = renderDetails({ input_json: JSON.stringify({ message: text }), params_json: JSON.stringify({ text }) })
    expect(screen.getByRole('region', { name: '输入文本内容' }).textContent).toBe(text)
    expect(container.querySelector('img, script')).toBeNull()
  })
})
