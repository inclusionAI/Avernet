// @vitest-environment jsdom
import React from 'react'
import { File as NodeFile } from 'node:buffer'
import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { EvolveTask } from '../../api/client'
import SkillTaskRuntimePanel, { StepInteractions } from '../SkillTaskRuntimePanel'
import { answeredInteractionHtml } from '../answered-interaction-html'

const api = vi.hoisted(() => ({ evolve: { answerStageInteraction: vi.fn(), getStageFeedbackUploadUrl: vi.fn(), getTaskSkillDiff: vi.fn() } }))
vi.mock('../../api/client', () => ({ api }))
const onUpdated = vi.fn(async () => {})
function task(status = 'answered', html = true): EvolveTask {
  return { task_id: 'TEST-1', task_type: 'stage_test', status: status === 'answered' ? 'completed' : 'waiting_context',
    config: { targetSkill: { candidate: { artifact: { ref: 'historical.zip' } } } },
    interactions: [{ interactionId: 'HITL-1', stepId: 'STEP-1', status,
      question: { format: html ? 'html' : 'text', tag: 'scope', content: html ? '<form><h3>检查范围</h3><label for="session">会话</label><input id="session" name="session"><button>提交</button></form>' : '检查哪些会话？' },
      answer: status === 'answered' ? { session: 'session-real-1' } : null }],
  } as unknown as EvolveTask
}
beforeEach(() => { vi.stubGlobal('React', React); vi.resetAllMocks(); api.evolve.answerStageInteraction.mockResolvedValue({ ok: true }) })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Stage interactions belong to their execution step', () => {
  it('keeps answered HTML layout with filled read-only controls in its step', () => {
    const value = task()
    const view = render(<><SkillTaskRuntimePanel task={value} canOperate onUpdated={onUpdated} /><section data-testid="step"><StepInteractions task={value} stepId="STEP-1" canOperate onUpdated={onUpdated} /></section></>)
    expect(screen.getByText('已回答')).toBeTruthy()
    const frame = screen.getByTitle('Stage 已回答的交互表单') as HTMLIFrameElement
    expect(frame.closest('[data-testid="step"]')).toBeTruthy()
    expect(frame.getAttribute('sandbox')).toBe('allow-same-origin')
    expect(frame.srcdoc).toContain('data-evolve-form-theme="platform"')
    const doc = new DOMParser().parseFromString(frame.srcdoc, 'text/html')
    expect(doc.querySelector('form h3')?.textContent).toBe('检查范围')
    expect(doc.querySelector('label')?.textContent).toBe('会话')
    expect(doc.querySelector('input')?.value).toBe('session-real-1')
    expect(doc.querySelector('input')?.disabled).toBe(true)
    expect(doc.querySelector('button')?.disabled).toBe(true)
    expect(doc.querySelector('script')).toBeNull()
    fireEvent(window, new MessageEvent('message', { source: frame.contentWindow, data: { channel: 'evolve-hitl:HITL-1', type: 'submit', value: { session: 'changed' } } }))
    expect(api.evolve.answerStageInteraction).not.toHaveBeenCalled()
    expect(screen.queryByText('需要补充信息')).toBeNull()
    expect(screen.queryByText('Skill 候选版本')).toBeNull()
    expect(api.evolve.getTaskSkillDiff).not.toHaveBeenCalled()
    const details = screen.getByText('交互技术详情').closest('details')!
    expect(details.open).toBe(false)
    fireEvent.click(screen.getByText('交互技术详情'))
    expect(details.open).toBe(true)
    expect(details.textContent).toContain('"session": "session-real-1"')
  })

  it('restores enveloped answers and preserves HTML history for read-only viewers', () => {
    const value = task()
    value.interactions![0].answer = { tag: 'scope', fields: { session: 'saved-envelope-answer' } }
    render(<StepInteractions task={value} stepId="STEP-1" canOperate={false} onUpdated={onUpdated} />)
    const frame = screen.getByTitle('Stage 已回答的交互表单') as HTMLIFrameElement
    const doc = new DOMParser().parseFromString(frame.srcdoc, 'text/html')
    expect(doc.querySelector('input')?.value).toBe('saved-envelope-answer')
    expect(doc.querySelector('input')?.readOnly).toBe(true)
  })

  it('keeps opaque submitted context out of the visible answer when the complete HTML form is available', () => {
    const value = task()
    value.interactions![0].question.content = '<form><h3>加固确认</h3><label>操作<select name="action"><option value="">请选择</option><option value="apply">批准并修改</option></select></label><input type="hidden" name="review_payload"><button>提交</button></form>'
    value.interactions![0].answer = { action: 'apply', review_payload: { version: 1, review: { inventory: 'large internal payload' } } }
    render(<StepInteractions task={value} stepId="STEP-1" canOperate={false} onUpdated={onUpdated} />)
    const frame = screen.getByTitle('Stage 已回答的交互表单') as HTMLIFrameElement
    const doc = new DOMParser().parseFromString(frame.srcdoc, 'text/html')
    expect(doc.querySelector<HTMLSelectElement>('select[name=action]')?.value).toBe('apply')
    expect(doc.body.getAttribute('data-evolve-form-theme')).toBe('platform')
    expect(screen.queryByText('已提交答案')).toBeNull()
    const details = screen.getByText('交互技术详情').closest('details')!
    expect(details.open).toBe(false)
  })

  it('restores textarea, repeated fields, radio, checkbox and select answers without executing HTML', () => {
    const content = `<style>.question{color:navy}</style><form class="question" action="https://invalid.test">
      <textarea name="notes">old</textarea><input name="repeat"><input name="repeat">
      <input type="checkbox" name="checks" value="a"><input type="checkbox" name="checks" value="b" checked>
      <input type="radio" name="choice" value="x" checked><input type="radio" name="choice" value="y">
      <select name="mode"><option value="old" selected>旧</option><option value="new">新</option></select>
      <select name="multi" multiple><option value="a">A</option><option value="b">B</option></select>
      <input name="missing" value="not submitted"><select name="absent"><option>Not submitted</option></select>
      <button onclick="alert(1)">提交</button><div contenteditable="true">说明</div>
      <script>window.parent.postMessage('submit','*')</script><iframe src="https://invalid.test"></iframe><a href="https://invalid.test">链接</a></form>`
    const answer = { notes: '</textarea><script>alert(1)</script>', repeat: ['one', 'two'], checks: ['a'], choice: 'y', mode: 'new', multi: ['a', 'b'], unknown: 'keep me' }
    const result = answeredInteractionHtml(content, answer)
    const doc = new DOMParser().parseFromString(result.html, 'text/html')
    expect(doc.querySelector('style')?.textContent).toContain('.question{color:navy}')
    expect(doc.querySelector('form')?.className).toBe('question')
    expect(doc.querySelector('textarea')?.value).toBe(answer.notes)
    expect(Array.from(doc.querySelectorAll<HTMLInputElement>('input[name=repeat]')).map((el) => el.value)).toEqual(['one', 'two'])
    expect(Array.from(doc.querySelectorAll<HTMLInputElement>('input[type=checkbox]')).map((el) => el.checked)).toEqual([true, false])
    expect(Array.from(doc.querySelectorAll<HTMLInputElement>('input[type=radio]')).map((el) => el.checked)).toEqual([false, true])
    expect(doc.querySelector<HTMLSelectElement>('select[name=mode]')?.value).toBe('new')
    expect(Array.from(doc.querySelector<HTMLSelectElement>('select[name=multi]')!.selectedOptions).map((el) => el.value)).toEqual(['a', 'b'])
    expect(doc.querySelector<HTMLInputElement>('input[name=missing]')?.value).toBe('')
    expect(doc.querySelector<HTMLSelectElement>('select[name=absent]')?.selectedOptions[0].text).toBe('（未填写）')
    expect(doc.querySelector('script,iframe,[onclick],[href],[action],[contenteditable=true]')).toBeNull()
    expect(Array.from(doc.querySelectorAll<HTMLInputElement>('input,select,textarea,button')).every((el) => el.disabled)).toBe(true)
    expect(result.remainingAnswer).toEqual({ unknown: 'keep me' })
  })

  it('does not repeat another step’s answers', () => {
    const view = render(<StepInteractions task={task()} stepId="OTHER-STEP" canOperate onUpdated={onUpdated} />)
    expect(view.container.textContent).toBe('')
  })

  it('only links to pending interactions at the top and submits from the matching step', async () => {
    const value = task('waiting', false)
    render(<><SkillTaskRuntimePanel task={value} canOperate onUpdated={onUpdated} /><StepInteractions task={value} stepId="STEP-1" canOperate onUpdated={onUpdated} /></>)
    expect(screen.getByRole('link', { name: '前往待回答步骤' }).getAttribute('href')).toBe('#step-STEP-1')
    fireEvent.change(screen.getByPlaceholderText('填写回答'), { target: { value: '只检查最近会话' } })
    fireEvent.click(screen.getByRole('button', { name: '提交并继续' }))
    await waitFor(() => expect(onUpdated).toHaveBeenCalledTimes(1))
    expect(api.evolve.answerStageInteraction).toHaveBeenCalledExactlyOnceWith('TEST-1', 'STEP-1', 'HITL-1', { tag: 'scope', content: '只检查最近会话' })
  })

  it('shows a completed Stage result decision and starts another logical round with text feedback', async () => {
    const value = task('waiting', false)
    value.interactions![0] = {
      ...value.interactions![0], interactionId: 'LOOP-1',
      question: {
        kind: 'loop_feedback', action: 'request_feedback',
        prompt: '接受当前加固结果，或补充意见后再处理一轮。',
        accepts: { text: true, files: ['.txt'] },
      },
    } as never
    render(<StepInteractions task={value} stepId="STEP-1" canOperate onUpdated={onUpdated} />)
    expect(screen.getByText('确认本轮处理结果')).toBeTruthy()
    expect(screen.getByText('接受当前加固结果，或补充意见后再处理一轮。')).toBeTruthy()
    fireEvent.change(screen.getByPlaceholderText('如需继续处理，请填写修改意见'), { target: { value: '保留原来的异常处理说明' } })
    fireEvent.click(screen.getByRole('button', { name: '提交反馈，再处理一轮' }))
    await waitFor(() => expect(api.evolve.answerStageInteraction).toHaveBeenCalledExactlyOnceWith(
      'TEST-1', 'STEP-1', 'LOOP-1', {
        action: 'continue', feedback: { text: '保留原来的异常处理说明', files: [] },
      },
    ))
    expect(onUpdated).toHaveBeenCalledTimes(1)
  })

  it('hashes and uploads a feedback file before submitting it together with text', async () => {
    vi.stubGlobal('crypto', webcrypto)
    const upload = vi.fn(async () => ({ ok: true }))
    vi.stubGlobal('fetch', upload)
    const value = task('waiting', false)
    value.interactions![0] = {
      ...value.interactions![0], interactionId: 'LOOP-1',
      question: { kind: 'loop_feedback', action: 'request_feedback', prompt: '请复核附件', accepts: { text: true, files: ['.txt'] } },
    } as never
    const file = new NodeFile(['abc'], 'review.txt', { type: 'text/plain' })
    const sha256 = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
    const artifact = { name: file.name, size: 3, sha256, ref: 'feedback/review.txt' }
    api.evolve.getStageFeedbackUploadUrl.mockResolvedValue({ url: '/signed-upload', method: 'PUT', headers: { 'Content-Type': 'text/plain' }, artifact })
    render(<StepInteractions task={value} stepId="STEP-1" canOperate onUpdated={onUpdated} />)
    fireEvent.change(screen.getByLabelText(/添加反馈文件/), { target: { files: [file] } })
    fireEvent.change(screen.getByPlaceholderText('如需继续处理，请填写修改意见'), { target: { value: '按附件复核' } })
    fireEvent.click(screen.getByRole('button', { name: '提交反馈，再处理一轮' }))
    await waitFor(() => expect(onUpdated).toHaveBeenCalledTimes(1))
    expect(api.evolve.getStageFeedbackUploadUrl).toHaveBeenCalledExactlyOnceWith('TEST-1', 'STEP-1', 'LOOP-1', { name: 'review.txt', size: 3, sha256, contentType: 'text/plain' })
    expect(upload).toHaveBeenCalledExactlyOnceWith('/signed-upload', { method: 'PUT', headers: { 'Content-Type': 'text/plain' }, body: file })
    expect(api.evolve.answerStageInteraction).toHaveBeenCalledExactlyOnceWith('TEST-1', 'STEP-1', 'LOOP-1', { action: 'continue', feedback: { text: '按附件复核', files: [artifact] } })
    expect(upload.mock.invocationCallOrder[0]).toBeLessThan(api.evolve.answerStageInteraction.mock.invocationCallOrder[0])
  })

  it('renders answered Loop feedback as business content without exposing storage metadata', () => {
    const value = task('answered', false)
    value.interactions![0] = {
      ...value.interactions![0], interactionId: 'LOOP-1', createdAt: 1_726_580_000, updatedAt: 1_726_580_100,
      question: {
        kind: 'loop_feedback', action: 'request_feedback',
        prompt: '请确认当前加固结果；如需调整，可补充意见或上传资料。',
        accepts: { text: true, files: ['.txt'] },
      },
      answer: {
        action: 'continue',
        feedback: {
          text: '保留原始语义，并补充异常处理说明。',
          files: [{
            name: 'review.txt', content_type: 'text/plain', size: 85,
            sha256: 'a'.repeat(64), ref: 'oss://private/internal-review.txt',
          }],
        },
      },
    } as never
    render(<StepInteractions task={value} stepId="STEP-1" canOperate={false} onUpdated={onUpdated} />)
    expect(screen.getByText('用户反馈')).toBeTruthy()
    expect(screen.getByText('继续调整，再处理一轮')).toBeTruthy()
    expect(screen.getByText('保留原始语义，并补充异常处理说明。')).toBeTruthy()
    expect(screen.getByText('review.txt')).toBeTruthy()
    expect(screen.getByText('85 B')).toBeTruthy()
    expect(screen.queryByText('continue')).toBeNull()
    expect(screen.queryByText('content_type')).toBeNull()
    expect(screen.queryByText('sha256')).toBeNull()
    expect(screen.queryByText('oss://private/internal-review.txt')).toBeNull()
  })

  it('ignores HTML messages from any other frame and preserves the sandbox', async () => {
    const view = render(<StepInteractions task={task('waiting')} stepId="STEP-1" canOperate onUpdated={onUpdated} />)
    const frame = view.container.querySelector('iframe')!
    expect(frame.getAttribute('sandbox')).toBe('allow-forms allow-scripts')
    expect(frame.srcdoc).toContain('data-evolve-form-theme="platform"')
    expect(frame.style.height).toBe('720px')
    fireEvent(window, new MessageEvent('message', { source: frame.contentWindow, data: { channel: 'evolve-hitl:HITL-1', type: 'resize', height: 900 } }))
    expect(frame.style.height).toBe('924px')
    fireEvent(window, new MessageEvent('message', { source: window, data: { channel: 'evolve-hitl:HITL-1', type: 'submit', value: { session: 'wrong' } } }))
    expect(api.evolve.answerStageInteraction).not.toHaveBeenCalled()
    fireEvent(window, new MessageEvent('message', { source: frame.contentWindow, data: { channel: 'evolve-hitl:HITL-1', type: 'submit', value: { session: 'right' } } }))
    await waitFor(() => expect(api.evolve.answerStageInteraction).toHaveBeenCalledExactlyOnceWith('TEST-1', 'STEP-1', 'HITL-1', { session: 'right' }))
  })

  it('preserves canceled-step questions as closed history without pending alerts or reply controls', () => {
    const value = task('waiting', false)
    value.status = 'running'
    value.steps = [{ stepId: 'STEP-1', status: 'canceled' }] as EvolveTask['steps']
    render(<><SkillTaskRuntimePanel task={value} canOperate onUpdated={onUpdated} />
      <StepInteractions task={value} stepId="STEP-1" canOperate onUpdated={onUpdated} /></>)
    expect(screen.getByText('检查哪些会话？')).toBeTruthy()
    expect(screen.getByText('已结束 · 未回答')).toBeTruthy()
    expect(screen.queryByText(/项信息待补充/)).toBeNull()
    expect(screen.queryByRole('textbox', { name: '用户回复' })).toBeNull()
    expect(screen.queryByRole('button', { name: '提交并继续' })).toBeNull()
  })

  it('hides obsolete Loop actions when the task is terminal', () => {
    const value = task('waiting', false)
    value.status = 'canceled'
    value.interactions![0].question = { kind: 'loop_feedback', prompt: '继续处理吗？', accepts: { text: true, files: ['.txt'] } } as never
    render(<><SkillTaskRuntimePanel task={value} canOperate onUpdated={onUpdated} />
      <StepInteractions task={value} stepId="STEP-1" canOperate onUpdated={onUpdated} /></>)
    expect(screen.getByText('继续处理吗？')).toBeTruthy()
    expect(screen.getByText('已结束 · 未回答')).toBeTruthy()
    expect(screen.queryByText(/项信息待补充/)).toBeNull()
    expect(screen.queryByRole('button', { name: '接受当前结果' })).toBeNull()
    expect(screen.queryByRole('button', { name: '提交反馈，再处理一轮' })).toBeNull()
  })

  it('shows saved answers when a waiting form becomes answered without remounting', () => {
    const value = task('waiting', false)
    value.interactions![0].question = {
      format: 'form', title: '方案确认', questions: [{ id: 'tier', type: 'single_choice', title: '加固等级', required: true,
        options: [{ value: 'tier1', label: '基础加固' }, { value: 'tier2', label: '深度加固' }] }],
    }
    const view = render(<StepInteractions task={value} stepId="STEP-1" canOperate onUpdated={onUpdated} />)
    fireEvent.click(screen.getByRole('radio', { name: '深度加固' }))
    const answered = { ...value, interactions: [{ ...value.interactions![0], status: 'answered',
      answer: { answers: { tier: { value: 'tier1', comment: '服务端已保存的确认' } } } }] }
    view.rerender(<StepInteractions task={answered} stepId="STEP-1" canOperate onUpdated={onUpdated} />)
    expect((screen.getByRole('radio', { name: '基础加固' }) as HTMLInputElement).checked).toBe(true)
    expect((screen.getByRole('radio', { name: '深度加固' }) as HTMLInputElement).checked).toBe(false)
    expect(screen.getByRole('radio', { name: '基础加固' }).matches(':disabled')).toBe(true)
    expect((screen.getByRole('textbox', { name: '加固等级补充意见' }) as HTMLTextAreaElement).value).toBe('服务端已保存的确认')
  })

  it('isolates radio groups across history and current interactions with the same question id', async () => {
    const value = task('waiting', false)
    const question = { format: 'form', title: '方案', questions: [{ id: 'tier', type: 'single_choice', title: '加固等级', required: true,
      options: [{ value: 'tier1', label: '基础加固' }, { value: 'tier2', label: '深度加固' }] }] }
    value.interactions = [
      { interactionId: 'OLD', stepId: 'OLD-STEP', status: 'answered', question, answer: { answers: { tier: { value: 'tier1' } } } },
      { interactionId: 'NEW', stepId: 'NEW-STEP', status: 'waiting', question, answer: null },
    ] as never
    render(<><section data-testid="history"><StepInteractions task={value} stepId="OLD-STEP" canOperate onUpdated={onUpdated} /></section>
      <section data-testid="current"><StepInteractions task={value} stepId="NEW-STEP" canOperate onUpdated={onUpdated} /></section></>)
    const history = within(screen.getByTestId('history'))
    const current = within(screen.getByTestId('current'))
    fireEvent.click(current.getByRole('radio', { name: '深度加固' }))
    expect((history.getByRole('radio', { name: '基础加固' }) as HTMLInputElement).checked).toBe(true)
    expect((current.getByRole('radio', { name: '深度加固' }) as HTMLInputElement).checked).toBe(true)
    fireEvent.click(current.getByRole('button', { name: '提交并继续' }))
    await waitFor(() => expect(api.evolve.answerStageInteraction).toHaveBeenCalledWith('TEST-1', 'NEW-STEP', 'NEW', { answers: { tier: { value: 'tier2', comment: '' } } }))
  })

  it('keeps collapsed read-only materials and the question form in independent paginated sections', async () => {
    const value = task('waiting', false)
    value.interactions![0].question = {
      format: 'form', title: '确认加固方案', description: '共 6 项，请逐项确认。',
      contents: [
        { id: 'current-state', title: '目标现状', format: 'markdown', content: '## 当前结构\n\n保留 **原始业务语义**。' },
        { id: 'inventory', title: '升级问题清单', format: 'markdown', content: '## P0\n\n仅现场确认必须拍板的问题。' },
      ],
      questions: [
        { id: 'tier', type: 'single_choice', title: '加固等级', required: true, options: [{ value: 'tier1', label: '基础加固', recommended: true }, { value: 'tier2', label: '深度加固' }] },
        { id: 'scope', type: 'multiple_choice', title: '重点范围', required: true, options: [{ value: 'sop', label: 'SOP' }, { value: 'errors', label: '异常处理' }] },
        { id: 'q3', type: 'short_text', title: '业务名称', required: true },
        { id: 'q4', type: 'long_text', title: '保留规则', required: false },
        { id: 'q5', type: 'short_text', title: '验收人', required: false },
        { id: 'q6', type: 'single_choice', title: '是否继续', required: true, options: [{ value: 'yes', label: '继续' }, { value: 'no', label: '停止' }] },
      ],
    } as never
    render(<StepInteractions task={value} stepId="STEP-1" canOperate onUpdated={onUpdated} />)
    const materials = screen.getByText('只读内容').closest('details')!
    expect(materials.open).toBe(false)
    const form = screen.getByRole('region', { name: '待回答问题' })
    expect(within(form).getByText('加固等级')).toBeTruthy()
    fireEvent.click(screen.getByText('只读内容'))
    expect(within(materials).getByText('目标现状')).toBeTruthy()
    expect(within(materials).getByText('原始业务语义')).toBeTruthy()
    fireEvent.click(within(materials).getByRole('button', { name: '下一页' }))
    expect(within(materials).getByText('升级问题清单')).toBeTruthy()
    expect(within(form).getByText('第 1 / 2 页')).toBeTruthy()
    expect(screen.getByText('加固等级')).toBeTruthy()
    expect(screen.queryByText('是否继续')).toBeNull()
    fireEvent.click(screen.getByLabelText('基础加固'))
    fireEvent.change(screen.getByLabelText('加固等级补充意见'), { target: { value: '保留现有业务语义' } })
    fireEvent.click(screen.getByLabelText('SOP'))
    fireEvent.change(screen.getByLabelText('业务名称'), { target: { value: '日报整理' } })
    fireEvent.click(within(form).getByRole('button', { name: '下一页' }))
    expect(within(materials).getByText('升级问题清单')).toBeTruthy()
    expect(screen.getByText('是否继续')).toBeTruthy()
    expect(screen.queryByText('加固等级')).toBeNull()
    fireEvent.click(screen.getByLabelText('继续'))
    fireEvent.click(screen.getByRole('button', { name: '提交并继续' }))
    await waitFor(() => expect(api.evolve.answerStageInteraction).toHaveBeenCalledExactlyOnceWith(
      'TEST-1', 'STEP-1', 'HITL-1', {
        answers: {
          tier: { value: 'tier1', comment: '保留现有业务语义' },
          scope: { value: ['sop'], comment: '' },
          q3: { value: '日报整理' },
          q4: { value: '' },
          q5: { value: '' },
          q6: { value: 'yes', comment: '' },
        },
      },
    ))
  })
})
