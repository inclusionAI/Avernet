/** @jest-environment jsdom */
import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import CreateBotModal from '@/components/BotWorkshop/CreateBotModal';
import type { AgentCodingTemplate } from '@/services/botWorkshop/agentCodingTemplateService';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

const makeTemplate = (): AgentCodingTemplate => ({
  key: 'architect',
  versionId: 'version-1',
  name: '架构 Bot',
  description: '从仓库理解架构',
  engine: 'claude_code',
  templateType: 'architect',
  source: 'official',
  fields: [],
  config: {},
  raw: {},
  capabilityTags: [],
});

beforeEach(() => {
  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: class ResizeObserverMock {
      observe() {}

      unobserve() {}

      disconnect() {}
    },
  });
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  HTMLElement.prototype.scrollIntoView = jest.fn();
});

afterEach(() => {
  // 注入过 overlay 引擎清单的用例退出时还原 Open 默认，避免污染同文件后续用例。
  extendCapabilities({ getBotEngineOptions: defaultCapabilities.getBotEngineOptions });
});

test('创建云端 Bot（Open Core 形态）卡片化展示 OpenClaw + Claude Code，不渲染内部引擎和引擎下拉', () => {
  render(
    <CreateBotModal
      scenario="cloud"
      spaces={[{ id: '10001', name: '个人空间', ownership: 'personal', canCreate: true }]}
      creating={false}
      onClose={jest.fn()}
      onSubmit={jest.fn()}
    />,
  );

  expect(screen.getByRole('button', { name: 'OpenClaw' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: 'Claude Code' })).toHaveAttribute('aria-pressed', 'false');
  expect(screen.getByText('通用 AI 对话助手，适用于日常工作与知识问答')).toBeInTheDocument();
  expect(screen.getByText('原生 Claude Code 引擎')).toBeInTheDocument();
  expect(screen.queryByText('Claudecode引擎-原生')).not.toBeInTheDocument();

  expect(screen.queryByRole('button', { name: 'AgentCoding' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Hermes' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'TEClaw' })).not.toBeInTheDocument();
  expect(screen.queryByRole('combobox', { name: '引擎类型' })).not.toBeInTheDocument();
});

test('capability 引擎清单为空时显示错误并禁用创建，不回退硬编码引擎', () => {
  extendCapabilities({
    getBotEngineOptions: () => ({ status: 'available', value: [] }),
  });
  render(
    <CreateBotModal
      scenario="cloud"
      spaces={[{ id: '10001', name: '个人空间', ownership: 'personal', canCreate: true }]}
      creating={false}
      onClose={jest.fn()}
      onSubmit={jest.fn()}
    />,
  );

  expect(screen.getByRole('alert')).toHaveTextContent('当前环境未提供可创建引擎');
  expect(screen.queryByRole('button', { name: 'OpenClaw' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '创建云端 Bot' })).toBeDisabled();
});

test('internal overlay 引擎卡片沿用 open-claw 描述，AgentCoding 展开后再次点击不折叠', () => {
  extendCapabilities({
    getBotEngineOptions: () => ({
      status: 'available',
      value: [
        { value: 'openclaw', label: 'OpenClaw', description: '通用 AI 对话助手，适用于日常工作与知识问答' },
        {
          value: 'aicoding',
          label: 'AgentCoding',
          description: '适合复杂研发任务，可理解代码仓库、规划方案并自动完成代码修改',
          createPanel: 'agent-coding',
        },
        { value: 'hermes', label: 'Hermes', description: 'Hermes agent，支持skill自进化' },
        {
          value: 'teclaw',
          label: 'TEClaw',
          description: '企业级分布式Agent，支持多租户隔离与高并发，数据持久化且权限可控',
        },
      ],
    }),
  });
  render(
    <CreateBotModal
      scenario="cloud"
      spaces={[{ id: '10001', name: '个人空间', ownership: 'personal', canCreate: true }]}
      creating={false}
      agentCodingTemplates={[]}
      onClose={jest.fn()}
      onSubmit={jest.fn()}
    />,
  );

  for (const option of ['OpenClaw', 'AgentCoding', 'Hermes', 'TEClaw']) {
    expect(screen.getByRole('button', { name: option })).toBeInTheDocument();
  }
  expect(screen.getByText('Hermes agent，支持skill自进化')).toBeInTheDocument();
  expect(screen.getByText('企业级分布式Agent，支持多租户隔离与高并发，数据持久化且权限可控')).toBeInTheDocument();
  expect(screen.queryByTestId('agent-coding-section')).not.toBeInTheDocument();

  const agentCoding = screen.getByRole('button', { name: 'AgentCoding' });
  fireEvent.click(agentCoding);
  expect(screen.getByTestId('agent-coding-section')).toBeInTheDocument();

  fireEvent.click(agentCoding);
  expect(screen.getByTestId('agent-coding-section')).toBeInTheDocument();
});

test('切换到普通引擎时收起 AgentCoding 面板并清理模板提交状态', async () => {
  extendCapabilities({
    getBotEngineOptions: () => ({
      status: 'available',
      value: [
        { value: 'openclaw', label: 'OpenClaw', description: '通用 AI 对话助手，适用于日常工作与知识问答' },
        { value: 'aicoding', label: 'AgentCoding', createPanel: 'agent-coding' },
      ],
    }),
  });
  const onSubmit = jest.fn();
  render(
    <CreateBotModal
      scenario="cloud"
      spaces={[{ id: '10001', name: '个人空间', ownership: 'personal', canCreate: true }]}
      creating={false}
      agentCodingTemplates={[makeTemplate()]}
      onClose={jest.fn()}
      onSubmit={onSubmit}
    />,
  );

  fireEvent.change(screen.getByLabelText(/Bot 名称/), { target: { value: '普通引擎 Bot' } });
  fireEvent.click(screen.getByRole('button', { name: 'AgentCoding' }));
  expect(screen.getByTestId('agent-coding-section')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'OpenClaw' }));
  expect(screen.queryByTestId('agent-coding-section')).not.toBeInTheDocument();
  expect(screen.getByRole('switch', { name: '是否提供服务' })).toHaveProperty('disabled', false);

  fireEvent.click(screen.getByRole('button', { name: '创建云端 Bot' }));
  await Promise.resolve();

  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ engine: 'openclaw', agentCoding: undefined }));
});

test('AgentCoding 未选择可服务化模板时，创建弹窗仍禁用服务开关', () => {
  extendCapabilities({
    getBotEngineOptions: () => ({
      status: 'available',
      value: [
        { value: 'openclaw', label: 'OpenClaw' },
        { value: 'aicoding', label: 'AgentCoding', createPanel: 'agent-coding' },
      ],
    }),
  });
  render(
    <CreateBotModal
      scenario="cloud"
      spaces={[{ id: '10001', name: '个人空间', ownership: 'personal', canCreate: true }]}
      creating={false}
      agentCodingTemplates={[makeTemplate()]}
      onClose={jest.fn()}
      onSubmit={jest.fn()}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: 'AgentCoding' }));

  expect(screen.getByRole('switch', { name: '是否提供服务' })).toHaveProperty('disabled', true);
  expect(screen.getByText('当前模板未开启服务 Bot 能力')).toBeInTheDocument();
});

test('归属空间与提供服务并排成同构字段：空间只读当前空间，提供服务为开关字段', () => {
  render(
    <CreateBotModal
      scenario="cloud"
      spaces={[{ id: '10002', name: '当前研发空间', ownership: 'team', canCreate: true }]}
      creating={false}
      onClose={jest.fn()}
      onSubmit={jest.fn()}
    />,
  );

  // 两者并排在同一个 grid 里，避免堆叠撑高弹窗
  const spaceLabel = screen.getByText('归属空间');
  const serviceLabel = screen.getByText('提供服务');
  const baseRow = spaceLabel.closest('.grid');
  expect(baseRow).toHaveClass('grid', 'sm:grid-cols-2');
  expect(serviceLabel.closest('.grid')).toBe(baseRow);

  // 归属空间：只读展示当前空间，非下拉
  expect(screen.getByText('当前研发空间')).toBeInTheDocument();
  expect(screen.getByText('跟随当前工作空间，不支持在创建时切换')).toBeInTheDocument();
  expect(screen.queryByRole('combobox', { name: '归属空间' })).not.toBeInTheDocument();

  // 提供服务：开关字段
  expect(screen.getByRole('switch', { name: '是否提供服务' })).toBeInTheDocument();
});
