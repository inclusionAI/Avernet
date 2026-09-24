/** @jest-environment jsdom */
import type { BotEngineOption } from '@/capabilities';
import { CreateBotEngineSelector } from '@/components/BotWorkshop/CreateBotModal/CreateBotEngineSelector';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const options: BotEngineOption[] = [
  {
    value: 'openclaw',
    label: 'OpenClaw 长文案',
    cardLabel: 'OpenClaw',
    description: '通用 AI 对话助手，适用于日常工作与知识问答',
  },
  {
    value: 'aicoding',
    label: 'AgentCoding',
    description: '适合复杂研发任务，可理解代码仓库、规划方案并自动完成代码修改',
    createPanel: 'agent-coding',
  },
];

const renderSelector = (value = 'openclaw', onChange = jest.fn()) =>
  render(
    <CreateBotEngineSelector
      options={options}
      value={value}
      onChange={onChange}
      renderPanel={(option) => (option.createPanel === 'agent-coding' ? <div>AgentCoding 展开内容</div> : null)}
    />,
  );

test('创建卡片优先使用 cardLabel，并展示 capability 描述', () => {
  renderSelector();

  expect(screen.getByRole('button', { name: 'OpenClaw' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'OpenClaw 长文案' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'AgentCoding' })).toBeInTheDocument();
  expect(screen.getByText('通用 AI 对话助手，适用于日常工作与知识问答')).toBeInTheDocument();
  expect(screen.getByText('适合复杂研发任务，可理解代码仓库、规划方案并自动完成代码修改')).toBeInTheDocument();
});

test('选中卡片暴露选中态，未配置展开面板的卡片不暴露 expanded', () => {
  renderSelector();

  const openClaw = screen.getByRole('button', { name: 'OpenClaw' });
  const agentCoding = screen.getByRole('button', { name: 'AgentCoding' });

  expect(openClaw).toHaveAttribute('aria-pressed', 'true');
  expect(openClaw).not.toHaveAttribute('aria-expanded');
  expect(agentCoding).toHaveAttribute('aria-pressed', 'false');
  expect(agentCoding).toHaveAttribute('aria-expanded', 'false');
  expect(openClaw.closest('div')).toHaveClass('border-primary/30', 'bg-primary/[0.04]');
});

test('AgentCoding 展开面板与卡片头部是兄弟节点，并通过 aria-controls 关联', () => {
  renderSelector('aicoding');

  const agentCoding = screen.getByRole('button', { name: 'AgentCoding' });
  const panel = screen.getByText('AgentCoding 展开内容').parentElement as HTMLElement;

  expect(agentCoding).toHaveAttribute('aria-expanded', 'true');
  expect(agentCoding).toHaveAttribute('aria-controls', panel.id);
  expect(panel).toBeInTheDocument();
  expect(panel.previousElementSibling).toBe(agentCoding);
  expect(agentCoding.contains(panel)).toBe(false);
});

test('再次点击已选中的 AgentCoding 卡片仍保持展开，切换事件不携带折叠语义', () => {
  const onChange = jest.fn();
  renderSelector('aicoding', onChange);
  const agentCoding = screen.getByRole('button', { name: 'AgentCoding' });

  fireEvent.click(agentCoding);
  expect(onChange).toHaveBeenLastCalledWith('aicoding');
  expect(screen.getByText('AgentCoding 展开内容')).toBeInTheDocument();
  expect(agentCoding).toHaveAttribute('aria-expanded', 'true');
});

test('Enter 和 Space 都可以触发卡片选择', async () => {
  const onChange = jest.fn();
  const user = userEvent.setup();
  renderSelector('aicoding', onChange);
  const openClaw = screen.getByRole('button', { name: 'OpenClaw' });

  openClaw.focus();
  await user.keyboard('{Enter}');
  expect(onChange).toHaveBeenLastCalledWith('openclaw');

  await user.keyboard(' ');
  expect(onChange).toHaveBeenLastCalledWith('openclaw');
});
