/** @jest-environment jsdom */
import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import CreateBotModal from '@/components/BotWorkshop/CreateBotModal';
import { BackendRequestError, backendRequest } from '@/services/backendApi/httpClient';
import {
  agentCodingTemplateService,
  type AgentCodingTemplate,
} from '@/services/botWorkshop/agentCodingTemplateService';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { history } from '@umijs/max';

jest.mock('@/services/botWorkshop/agentCodingTemplateService', () => {
  const actual = jest.requireActual('@/services/botWorkshop/agentCodingTemplateService');
  return {
    ...actual,
    agentCodingTemplateService: { ...actual.agentCodingTemplateService, list: jest.fn() },
  };
});

jest.mock('@/services/backendApi/httpClient', () => ({
  ...jest.requireActual('@/services/backendApi/httpClient'),
  backendRequest: jest.fn(),
}));

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));

const templateList = agentCodingTemplateService.list as jest.Mock;

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
  jest.mocked(backendRequest).mockReset();
  templateList.mockReset().mockResolvedValue([]);
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
  expect(screen.getByText('Bot 头像')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '上传图片' })).toBeInTheDocument();
  expect(screen.getAllByRole('button', { name: /选择头像/ })).toHaveLength(8);
});

test('创建 Bot 时上传图片会写入提交参数中的 avatarUrl', async () => {
  const onSubmit = jest.fn().mockResolvedValue(undefined);
  render(
    <CreateBotModal
      scenario="cloud"
      spaces={[{ id: '10001', name: '个人空间', ownership: 'personal', canCreate: true }]}
      creating={false}
      onClose={jest.fn()}
      onSubmit={onSubmit}
    />,
  );
  fireEvent.change(screen.getByPlaceholderText('例如：项目知识助手'), { target: { value: '头像 Bot' } });
  const input = screen.getByLabelText('上传 Bot 头像') as HTMLInputElement;
  const file = new File(['image'], 'avatar.png', { type: 'image/png' });
  fireEvent.change(input, { target: { files: [file] } });
  await waitFor(() =>
    expect(screen.getByAltText('当前 Bot 头像')).toHaveAttribute(
      'src',
      expect.stringMatching(/^data:image\/png;base64,/),
    ),
  );
  fireEvent.click(screen.getByRole('button', { name: '创建云端 Bot' }));

  expect(onSubmit).toHaveBeenCalledWith(
    expect.objectContaining({ name: '头像 Bot', avatarUrl: expect.stringMatching(/^data:image\/png;base64,/) }),
  );
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

test('internal overlay 引擎卡片沿用 open-claw 描述，选中 AgentCoding 后才加载并展开模板', async () => {
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
  templateList.mockResolvedValue([makeTemplate()]);
  render(
    <CreateBotModal
      scenario="cloud"
      spaces={[{ id: '10001', name: '个人空间', ownership: 'personal', canCreate: true }]}
      creating={false}
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
  expect(templateList).not.toHaveBeenCalled();

  const agentCoding = screen.getByRole('button', { name: 'AgentCoding' });
  fireEvent.click(agentCoding);
  expect(screen.getByTestId('agent-coding-section')).toBeInTheDocument();
  await screen.findByText('架构 Bot');
  expect(templateList).toHaveBeenCalledTimes(1);

  fireEvent.click(agentCoding);
  expect(screen.getByTestId('agent-coding-section')).toBeInTheDocument();
  expect(templateList).toHaveBeenCalledTimes(1);
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
      onClose={jest.fn()}
      onSubmit={onSubmit}
    />,
  );

  fireEvent.change(screen.getByLabelText(/Bot 名称/), { target: { value: '普通引擎 Bot' } });
  fireEvent.click(screen.getByRole('button', { name: 'AgentCoding' }));
  expect(screen.getByTestId('agent-coding-section')).toBeInTheDocument();
  await waitFor(() => expect(templateList).toHaveBeenCalledTimes(1));

  fireEvent.click(screen.getByRole('button', { name: 'OpenClaw' }));
  expect(screen.queryByTestId('agent-coding-section')).not.toBeInTheDocument();
  expect(screen.getByRole('switch', { name: '是否提供服务' })).toHaveProperty('disabled', false);

  fireEvent.click(screen.getByRole('button', { name: '创建云端 Bot' }));
  await Promise.resolve();

  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ engine: 'openclaw', agentCoding: undefined }));
});

test('AgentCoding 未选择可服务化模板时，创建弹窗仍禁用服务开关', async () => {
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
      onClose={jest.fn()}
      onSubmit={jest.fn()}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: 'AgentCoding' }));
  await waitFor(() => expect(templateList).toHaveBeenCalledTimes(1));

  expect(screen.getByRole('switch', { name: '是否提供服务' })).toHaveProperty('disabled', true);
  expect(screen.getByText('当前模板未开启服务 Bot 能力')).toBeInTheDocument();
});

test('归属空间可选择，提供服务沿原引擎能力', () => {
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
  expect(screen.getByRole('combobox', { name: '归属空间' })).toHaveValue('10002');
  expect(screen.getByRole('combobox', { name: '归属空间' })).toBeEnabled();

  // 提供服务：开关字段
  expect(screen.getByRole('switch', { name: '是否提供服务' })).toBeInTheDocument();
});

const identityExpert = {
  expert_id: 'product-manager',
  name: '产品经理',
  description: '梳理需求',
  category: '产品',
  tags: [],
  creation_count: 0,
  first_added_at: '2026-10-27',
};

test('详情入口固定专家，202后留弹窗等待原任务，Apply成功后展示完成', async () => {
  const request = jest.mocked(backendRequest);
  request.mockImplementation(async (path) =>
    path === '/openapi/v1/experts/product-manager/bots'
      ? { code: 200000, data: { bot_id: 'expert-bot', iframe_url: '', redirect_url: '' } }
      : {
          code: 200000,
          data: {
            state: 'READY',
            bot_id: 'expert-bot',
            bot: { bot_id: 'expert-bot' },
            iframe_url: '',
            redirect_url: '',
          },
        },
  );
  const onClose = jest.fn();
  const ordinaryCreate = jest.fn();
  render(
    <CreateBotModal
      scenario="cloud"
      expert={identityExpert}
      spaces={[
        { id: '10', name: '个人空间', ownership: 'personal', canCreate: true },
        { id: '20', name: '研发空间', ownership: 'team', canCreate: true },
      ]}
      creating={false}
      onClose={onClose}
      onSubmit={ordinaryCreate}
    />,
  );
  expect(screen.queryByRole('button', { name: '空白创建' })).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole('combobox', { name: '归属空间' }), { target: { value: '20' } });
  fireEvent.click(screen.getByRole('button', { name: '创建云端 Bot' }));
  fireEvent.click(screen.getByRole('button', { name: '创建云端 Bot' }));
  await screen.findByText('Bot 创建及配置已完成');
  expect(request.mock.calls.filter(([path]) => path === '/openapi/v1/experts/product-manager/bots')).toHaveLength(1);
  expect(request).toHaveBeenCalledWith(
    '/openapi/v1/experts/product-manager/bots',
    expect.objectContaining({
      data: {
        bot_name: '我的产品经理',
        bot_desc: '梳理需求',
        engine: 'openclaw',
        cluster_name: 'ACRA',
        bot_type: 'personal',
        space_id: '20',
      },
    }),
  );
  expect(onClose).not.toHaveBeenCalled();
  expect(ordinaryCreate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '留在当前页面' }));
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('管理入口两份草稿独立，换专家保留自定义名称和职责', async () => {
  jest.mocked(backendRequest).mockResolvedValue({
    code: 200000,
    data: {
      total: 2,
      items: [identityExpert, { ...identityExpert, expert_id: 'architect', name: '架构师', description: '设计架构' }],
    },
  });
  render(
    <CreateBotModal
      scenario="cloud"
      spaces={[{ id: '10', name: '个人空间', ownership: 'personal', canCreate: true }]}
      creating={false}
      onClose={jest.fn()}
      onSubmit={jest.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText(/Bot 名称/), { target: { value: '空白草稿' } });
  fireEvent.click(screen.getByRole('button', { name: '从专家模板创建' }));
  fireEvent.click(await screen.findByRole('button', { name: '产品经理' }));
  expect(screen.getByLabelText(/Bot 名称/)).toHaveValue('我的产品经理');
  fireEvent.change(screen.getByLabelText(/Bot 名称/), { target: { value: '我的助手' } });
  fireEvent.change(screen.getByPlaceholderText('简要说明这个 Bot 能帮助你完成什么'), {
    target: { value: '自定义职责' },
  });
  fireEvent.click(screen.getByRole('button', { name: '架构师' }));
  expect(screen.getByLabelText(/Bot 名称/)).toHaveValue('我的助手');
  expect(screen.getByPlaceholderText('简要说明这个 Bot 能帮助你完成什么')).toHaveValue('自定义职责');
  fireEvent.click(screen.getByRole('button', { name: '空白创建' }));
  expect(screen.getByLabelText(/Bot 名称/)).toHaveValue('空白草稿');
  fireEvent.click(screen.getByRole('button', { name: '从专家模板创建' }));
  expect(screen.getByLabelText(/Bot 名称/)).toHaveValue('我的助手');
});

test('首次POST断连且无句柄时不自动重发，并明确提示结果未知', async () => {
  const request = jest.mocked(backendRequest);
  request.mockReset().mockRejectedValue(new Error('网络连接中断'));
  render(
    <CreateBotModal
      scenario="cloud"
      expert={identityExpert}
      spaces={[{ id: '10', name: '个人空间', ownership: 'personal', canCreate: true }]}
      creating={false}
      onClose={jest.fn()}
      onSubmit={jest.fn()}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: '创建云端 Bot' }));
  await screen.findByText('首次提交结果未知，请先到 Bot 列表确认，避免重复创建。');
  expect(request).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('button', { name: '创建云端 Bot' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '前往 Bot 列表' }));
  expect(history.push).toHaveBeenLastCalledWith('/bot-workshop');
});

test('服务草稿存在但Apply失败时保留真实结果，不显示配置完成或重新创建', async () => {
  const request = jest.mocked(backendRequest);
  request
    .mockResolvedValueOnce({ code: 200000, data: { bot_id: 'service-bot', iframe_url: '', redirect_url: '' } })
    .mockResolvedValue({
      code: 200000,
      data: { state: 'APPLY_FAILED', bot_id: 'service-bot', bot: { bot_id: 'service-bot' }, message: '身份配置失败' },
    });
  render(
    <CreateBotModal
      scenario="cloud"
      expert={identityExpert}
      spaces={[{ id: '10', name: '个人空间', ownership: 'personal', canCreate: true }]}
      creating={false}
      onClose={jest.fn()}
      onSubmit={jest.fn()}
    />,
  );
  fireEvent.click(screen.getByRole('switch', { name: '是否提供服务' }));
  fireEvent.click(screen.getByRole('button', { name: '创建云端 Bot' }));
  await screen.findByText('Bot 已创建，但配置失败');
  expect(screen.getByText(/服务 Bot 草稿已创建，配置进度单独显示/)).toBeInTheDocument();
  expect(screen.getByText('身份配置失败')).toBeInTheDocument();
  expect(screen.queryByText('Bot 创建及配置已完成')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '创建云端 Bot' })).not.toBeInTheDocument();
});

test('同名409保留表单与用户输入，允许修正再提交', async () => {
  const request = jest.mocked(backendRequest);
  request.mockRejectedValue(
    new BackendRequestError('Bot名称已存在', { status: 409, apiPath: '/openapi/v1/experts/product-manager/bots' }),
  );
  render(
    <CreateBotModal
      scenario="cloud"
      expert={identityExpert}
      spaces={[{ id: '10', name: '个人空间', ownership: 'personal', canCreate: true }]}
      creating={false}
      onClose={jest.fn()}
      onSubmit={jest.fn()}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: '创建云端 Bot' }));
  await screen.findByText('Bot名称已存在');
  expect(screen.getByLabelText(/Bot 名称/)).toHaveValue('我的产品经理');
  expect(screen.getByRole('button', { name: '创建云端 Bot' })).toBeEnabled();
});

test('专家创建沿原AgentPass授权显示并继续轮询到Apply完成', async () => {
  jest.useFakeTimers();
  const request = jest.mocked(backendRequest);
  request
    .mockResolvedValueOnce({
      code: 200000,
      data: { bot_id: 'auth-bot', iframe_url: 'https://auth.example.com/consent', redirect_url: '' },
    })
    .mockResolvedValueOnce({
      code: 200000,
      data: {
        state: 'AWAITING_AUTHORIZATION',
        bot_id: 'auth-bot',
        iframe_url: 'https://auth.example.com/consent',
        redirect_url: '',
      },
    })
    .mockResolvedValue({
      code: 200000,
      data: { state: 'READY', bot_id: 'auth-bot', bot: { bot_id: 'auth-bot' }, iframe_url: '', redirect_url: '' },
    });
  const view = render(
    <CreateBotModal
      scenario="cloud"
      expert={identityExpert}
      spaces={[{ id: '10', name: '个人空间', ownership: 'personal', canCreate: true }]}
      creating={false}
      onClose={jest.fn()}
      onSubmit={jest.fn()}
    />,
  );
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '创建云端 Bot' }));
  });
  expect(screen.getByTitle('Bot 授权')).toHaveAttribute('src', 'https://auth.example.com/consent');
  await act(async () => {
    jest.advanceTimersByTime(2000);
  });
  expect(screen.queryByTitle('Bot 授权')).not.toBeInTheDocument();
  expect(screen.getByText('Bot 创建及配置已完成')).toBeInTheDocument();
  view.unmount();
  jest.useRealTimers();
});

test('模板切换只替换自动预填字段，取消和关闭重开不会保留草稿或创建Bot', async () => {
  const request = jest.mocked(backendRequest);
  request.mockResolvedValue({
    code: 200000,
    data: {
      total: 2,
      items: [identityExpert, { ...identityExpert, expert_id: 'architect', name: '架构师', description: '设计架构' }],
    },
  });
  const props = {
    spaces: [{ id: '10', name: '个人空间', ownership: 'personal' as const, canCreate: true }],
    creating: false,
    onClose: jest.fn(),
    onSubmit: jest.fn(),
  };
  const view = render(<CreateBotModal {...props} scenario="cloud" />);
  fireEvent.click(screen.getByRole('button', { name: '从专家模板创建' }));
  fireEvent.click(await screen.findByRole('button', { name: '产品经理' }));
  fireEvent.click(screen.getByRole('button', { name: '架构师' }));
  expect(screen.getByLabelText(/Bot 名称/)).toHaveValue('我的架构师');
  expect(screen.getByPlaceholderText('简要说明这个 Bot 能帮助你完成什么')).toHaveValue('设计架构');
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  expect(props.onClose).toHaveBeenCalledTimes(1);
  expect(request.mock.calls.some(([path]) => path.endsWith('/bots'))).toBe(false);
  view.rerender(<CreateBotModal {...props} />);
  view.rerender(<CreateBotModal {...props} scenario="cloud" />);
  expect(screen.getByLabelText(/Bot 名称/)).toHaveValue('');
  expect(screen.getByRole('button', { name: '空白创建' })).toHaveAttribute('aria-pressed', 'true');
});


test('详情与模板入口自动名称使用我的前缀且总长最多40字符', async () => {
  const props = { spaces: [{ id: '10', name: '个人空间', ownership: 'personal' as const, canCreate: true }], creating: false, onClose: jest.fn(), onSubmit: jest.fn() };
  render(<CreateBotModal {...props} scenario="cloud" expert={{ ...identityExpert, name: '长'.repeat(40) }} />);
  expect(screen.getByLabelText(/Bot 名称/)).toHaveValue('我的' + '长'.repeat(38));
});
