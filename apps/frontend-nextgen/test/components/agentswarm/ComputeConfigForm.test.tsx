/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { ComputeConfigForm } from '@/components/agentswarm/ComputeConfigForm';
import { createAgentDraft, type ComputeDraft } from '@/domain/agentswarm/draft';
import type { IdentityOption } from '@/domain/agentswarm/identityOptions';

// The YAML editor lazy-loads CodeMirror, which jest cannot import dynamically.
jest.mock('@/pages/Workspace/components/Modals/YamlEditor', () => ({
  YamlCodeEditor: ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    <textarea data-testid="yaml-code-editor" value={value} onChange={(event) => onChange(event.target.value)} />
  ),
}));

beforeAll(() => {
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  HTMLElement.prototype.scrollIntoView = jest.fn();
});

                                                      const options: IdentityOption[] = [
  { botId: 'b1', displayName: '数据分析助手', online: true, selectable: true },
  { botId: 'b2', displayName: '坏掉的 Bot', online: false, selectable: false, disabledReason: 'Bot 状态为 FAILED，暂不可用' },
];

const botDraft = (overrides: Partial<ComputeDraft> = {}): ComputeDraft => ({
  goal: '批量处理',
  agents: [createAgentDraft({ key: 'a', name: 'worker', role: 'works', kind: 'bot' })],
  identityBotId: undefined,
  ...overrides,
});

const renderForm = (draft: ComputeDraft, overrides: Partial<React.ComponentProps<typeof ComputeConfigForm>> = {}) =>
  render(
    <ComputeConfigForm
      draft={draft}
      onChange={jest.fn()}
      identityOptions={options}
      onSubmit={jest.fn()}
      {...overrides}
    />,
  );

test('the form follows the PRD structure: goal, agent definitions, limits, identity', () => {
  renderForm(botDraft());
  expect(screen.getByText('计算目标')).toBeInTheDocument();
  expect(screen.getByText(/Agent 定义 · 1/)).toBeInTheDocument();
  expect(screen.getByText(/自定义执行限制|执行限制/)).toBeInTheDocument();
  expect(screen.getByText('身份 Bot')).toBeInTheDocument();
});

test('each agent collapses to a "name · role" summary', () => {
  renderForm(botDraft());
  expect(screen.getByText('worker · works')).toBeInTheDocument();
});

test('an unnamed agent falls back to its position and a placeholder role', () => {
  renderForm(botDraft({ agents: [createAgentDraft({ key: 'a', kind: 'bot' })] }));
  expect(screen.getByText('Agent 1 · 待填写角色')).toBeInTheDocument();
});

test('名称 and 角色 are labelled fields with required marks', () => {
  renderForm(botDraft());
  fireEvent.click(screen.getByText('worker · works'));
  expect(screen.getByLabelText(/名称/)).toBeInTheDocument();
  expect(screen.getByLabelText(/角色/)).toBeInTheDocument();
  expect(screen.getByLabelText(/^指令$/)).toBeInTheDocument();
});

test('field errors appear at the field, not only in the footer', () => {
  const { container } = renderForm(botDraft({ goal: '', agents: [createAgentDraft({ key: 'a', name: 'worker', role: '', kind: 'bot' })] }));
  fireEvent.click(screen.getByText('提交计算任务'));
  const alerts = Array.from(container.querySelectorAll('[role="alert"]'));
  expect(alerts.length).toBeGreaterThan(1);
  expect(alerts.some((node) => node.textContent?.includes('请填写工作目标'))).toBe(true);
  expect(alerts.some((node) => node.textContent?.includes('身份 Bot'))).toBe(true);
});

test('a missing role is flagged on its own field', () => {
  const { container } = renderForm(botDraft({ agents: [createAgentDraft({ key: 'a', name: 'worker', role: '', kind: 'bot' })] }));
  fireEvent.click(screen.getByText('提交计算任务'));
  const roleInput = screen.getByLabelText(/角色/);
  expect(roleInput).toHaveAttribute('aria-invalid', 'true');
  expect(container.textContent).toContain('每个 Agent 都需要角色说明');
});

test('an untouched form is not a wall of errors', () => {
  renderForm(botDraft());
  expect(screen.queryAllByRole('alert')).toHaveLength(0);
});

test('only selectable Bots are offered — a disabled Bot is not listed at all', () => {
  renderForm({ ...botDraft(), identityBotId: 'b1' });
  // Radix renders the selected label in both the trigger and a hidden native
  // select, so assert on presence/absence rather than a single element.
  expect(screen.queryByText('坏掉的 Bot')).toBeNull();
  expect(screen.getAllByText('数据分析助手').length).toBeGreaterThan(0);
});

test('a seeded identity from the chat shows as the current choice', () => {
  renderForm({ ...botDraft(), identityBotId: '20261110_chatbot' });
  expect(screen.getAllByText('20261110_chatbot').length).toBeGreaterThan(0);
  expect(screen.queryByText(/选择用于继承身份的运行 Bot/)).toBeNull();
});

test('the empty state appears only when nothing is selectable', () => {
  renderForm(botDraft(), { identityOptions: [options[1]] });
  expect(screen.getByText(/还没有可用的 Bot/)).toBeInTheDocument();
});

test('an llm-only draft gets no identity control at all, not a dead one', () => {
  renderForm({
    goal: 'g',
    agents: [createAgentDraft({ key: 'l', name: 'planner', role: 'plans', kind: 'llm' })],
  });
  expect(screen.queryByText('身份 Bot')).toBeNull();
  expect(screen.queryByText(/无需选择身份 Bot/)).toBeNull();
});

test('submit is available once the draft is complete', () => {
  renderForm({ ...botDraft(), identityBotId: 'b1' });
  expect(screen.getByText('提交计算任务')).toBeEnabled();
});

test('adding an agent appends one with a generated unique name', () => {
  const onChange = jest.fn();
  renderForm({ ...botDraft(), identityBotId: 'b1' }, { onChange });
  fireEvent.click(screen.getByText('添加 Agent'));
  expect(onChange).toHaveBeenCalledWith(
    expect.objectContaining({ agents: expect.arrayContaining([expect.objectContaining({ name: 'agent_2' })]) }),
  );
});

test('no client-only concept is read from the draft', () => {
  const { container } = renderForm({ ...botDraft(), identityBotId: 'b1' });
  expect(container.textContent).not.toMatch(/待授权|AgentPass|STARTING|AUTHORIZATION/);
});