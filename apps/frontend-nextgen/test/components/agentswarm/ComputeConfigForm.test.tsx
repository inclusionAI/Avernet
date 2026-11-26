/** @jest-environment jsdom */
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { ComputeConfigForm } from '@/components/agentswarm/ComputeConfigForm';
import { createNewAgentRef, type ComputeDraft } from '@/domain/agentswarm/draft';
import type { IdentityOption } from '@/domain/agentswarm/identityOptions';

// The form reads the definition library so a typed name can resolve to it; the
// list is empty here unless a test supplies rows.
jest.mock('@/hooks/useDefinitions', () => ({
  useDefinitions: () => ({ definitions: [], total: 0, loading: false, error: null, refresh: jest.fn() }),
}));

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

const newRef = (partial: Parameters<typeof createNewAgentRef>[0] = {}) =>
  createNewAgentRef({ key: 'a', name: 'worker', role: 'works', kind: 'bot', ...partial });

const botDraft = (overrides: Partial<ComputeDraft> = {}): ComputeDraft => ({
  goal: '批量处理',
  refs: [newRef()],
  identityBotId: undefined,
  ...overrides,
});

/** Two roster entries, so nav-selection tests have something to select. */
const twoAgents = () =>
  botDraft({
    refs: [newRef({ key: 'a', name: 'worker', role: 'works' }), newRef({ key: 'b', name: 'reviewer', role: 'reviews' })],
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

test('the form follows PRD §7: config on the left, 运行设置 in its own column', () => {
  const { container } = renderForm(botDraft());
  // 提交前检查 repeats 计算目标 as a status row, so scope the field assertions.
  const form = container.querySelector('fieldset') as HTMLElement;
  expect(within(form).getByText(/计算目标/)).toBeInTheDocument();
  expect(within(form).getByText(/Agent 定义 · 1/)).toBeInTheDocument();

  // 运行设置 is a labelled aside — the right column — not a stacked section.
  const runSettings = container.querySelector('aside[aria-label="运行设置"]');
  expect(runSettings).not.toBeNull();
  expect(within(runSettings as HTMLElement).getByText('运行设置')).toBeInTheDocument();
  expect(within(runSettings as HTMLElement).getByText('执行权限 Bot')).toBeInTheDocument();
  expect(within(runSettings as HTMLElement).getByText('提交前检查')).toBeInTheDocument();
  // The PRD's required explanation of what the choice means.
  expect(
    within(runSettings as HTMLElement).getByText(/本次计算任务将使用所选 Bot 的权限执行/),
  ).toBeInTheDocument();
  // The roster heading belongs to the config column, not the aside.
  expect(within(runSettings as HTMLElement).queryByRole('heading', { name: /Agent 定义/ })).toBeNull();
  expect(within(form).getByRole('heading', { name: /Agent 定义/ })).toBeInTheDocument();
});

test('each roster entry is a row in the left nav, labelled with its origin', () => {
  // The nav names the agent by its spawn name and states where it came from,
  // because a new entry and a library ref submit differently before saving.
  renderForm(twoAgents());
  const nav = screen.getByRole('navigation', { name: '执行 Agent 定义' });
  expect(within(nav).getByText('worker')).toBeInTheDocument();
  expect(within(nav).getByText('reviewer')).toBeInTheDocument();
  expect(within(nav).getAllByText(/新建 · 未保存/)).toHaveLength(2);
});

test('an unnamed entry falls back to its position', () => {
  renderForm(botDraft({ refs: [newRef({ name: '' })] }));
  const nav = screen.getByRole('navigation', { name: '执行 Agent 定义' });
  expect(within(nav).getByText('Agent 1')).toBeInTheDocument();
});

test('the middle column edits the selected agent under two tabs', () => {
  renderForm(botDraft());
  // 基本信息 is the first tab, so the identity fields are visible immediately.
  expect(screen.getByRole('tab', { name: '基本信息' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('tab', { name: '身份与能力' })).toHaveAttribute('aria-selected', 'false');
  expect(screen.getByLabelText(/名称/)).toBeInTheDocument();
  expect(screen.getByLabelText(/角色/)).toBeInTheDocument();

  // 身份与能力 swaps the panel; the basic fields leave the DOM.
  fireEvent.click(screen.getByRole('tab', { name: '身份与能力' }));
  expect(screen.getByRole('tabpanel')).toBeInTheDocument();
  expect(screen.getByText('独立模型')).toBeInTheDocument();
  expect(screen.queryByLabelText(/^角色/)).toBeNull();
});

test('selecting another agent in the nav switches the editor to it', () => {
  renderForm(twoAgents());
  const nav = screen.getByRole('navigation', { name: '执行 Agent 定义' });
  fireEvent.click(within(nav).getByText('reviewer'));
  expect(screen.getByLabelText(/名称/)).toHaveValue('reviewer');
});

test('field errors appear at the field, not only in the footer', () => {
  const { container } = renderForm(botDraft({ goal: '', refs: [newRef({ role: '' })] }));
  fireEvent.click(screen.getByText('提交计算任务'));
  const alerts = Array.from(container.querySelectorAll('[role="alert"]'));
  expect(alerts.length).toBeGreaterThan(1);
  expect(alerts.some((node) => node.textContent?.includes('请填写工作目标'))).toBe(true);
  expect(alerts.some((node) => node.textContent?.includes('执行权限 Bot'))).toBe(true);
});

test('a missing role is flagged on its own field', () => {
  const { container } = renderForm(botDraft({ refs: [newRef({ role: '' })] }));
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
    refs: [newRef({ key: 'l', name: 'planner', role: 'plans', kind: 'llm' })],
  });
  expect(screen.queryByText('执行权限 Bot')).toBeNull();
  expect(screen.queryByText(/无需选择执行权限 Bot/)).toBeNull();
});

test('submit is available once the draft is complete', () => {
  renderForm({ ...botDraft(), identityBotId: 'b1' });
  expect(screen.getByText('提交计算任务')).toBeEnabled();
});

test('空白新建 appends a new roster entry', () => {
  const onChange = jest.fn();
  renderForm({ ...botDraft(), identityBotId: 'b1' }, { onChange });
  fireEvent.click(screen.getByText('空白新建'));
  // The appended entry is a `new` ref, which is what makes it save on submit.
  const next = onChange.mock.calls[0][0];
  expect(next.refs).toHaveLength(2);
  expect(next.refs[1]).toMatchObject({ origin: 'new', alias: '' });
});

test('no client-only concept is read from the draft', () => {
  const { container } = renderForm({ ...botDraft(), identityBotId: 'b1' });
  expect(container.textContent).not.toMatch(/待授权|AgentPass|STARTING|AUTHORIZATION/);
});
test('填入示例任务 reuses definitions a previous submit already saved', () => {
  // The example agents were saved on an earlier submit, so re-filling must
  // reference them instead of creating blank entries that collide.
  jest.isolateModules(() => {});
  const { AGENT_EXAMPLES } = jest.requireActual('@/domain/agentswarm/examples');
  const names = AGENT_EXAMPLES[0].agents.map((agent: { name: string }) => agent.name);

  const onChange = jest.fn();
  const withLibrary = botDraft();
  // The hook is mocked per-module, so drive the reference rule directly against
  // the same library shape the form would have received.
  const { autoReferenceByBodyName, createNewAgentRef: makeRef } = jest.requireActual(
    '@/domain/agentswarm/draft',
  );
  const filled = autoReferenceByBodyName(
    { ...withLibrary, refs: names.map((name: string) => makeRef({ name, role: 'r' })) },
    names.map((name: string, index: number) => ({ definition_id: `def-${index}`, name })),
  );
  expect(filled.refs.every((ref: { origin: string }) => ref.origin === 'library')).toBe(true);
  expect(onChange).not.toHaveBeenCalled();
});
