/** @jest-environment jsdom */

import { ContainerInstancesModal } from '@/components/BotWorkshop/ContainerInstancesModal';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import { botContainerService } from '@/services/botWorkshop/botContainerService';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/services/botWorkshop/botContainerService', () => ({
  botContainerService: { list: jest.fn(), restart: jest.fn(), scale: jest.fn() },
}));

const list = botContainerService.list as jest.Mock;
const scale = botContainerService.scale as jest.Mock;
const getSummaryText = (value: string) =>
  screen.getByText((_, element) => element?.textContent === value);

describe('ContainerInstancesModal', () => {
  beforeEach(() => {
    list.mockReset();
    scale.mockReset();
  });

  it('Teclaw 即使被传入详情弹窗也不查询容器', () => {
    const bot = mapBotDto({ bot_id: 'teclaw-1', engine: 'teclaw', bot_type: 'service' }).item;
    render(<ContainerInstancesModal bot={bot} userId="user-1" onClose={jest.fn()} />);
    expect(list).not.toHaveBeenCalled();
  });

  it('renders the legacy-style instance fields and does not invent absent resource metrics', async () => {
    list.mockResolvedValue({
      botId: 'service-1',
      summary: { total: 2, healthy: 1, abnormal: 1, restarting: 0, unknown: 0 },
      instances: [
        { id: 'device-1', engine: 'openclaw', status: 'healthy' },
        { id: 'device-2', engine: 'openclaw', status: 'abnormal', node: 'node-a', cpu: '12%', memory: '128 MB' },
      ],
    });
    const bot = mapBotDto({
      bot_id: 'service-1',
      bot_name: '服务 Bot',
      bot_type: 'service',
      engine: 'openclaw',
      display_state: 'service_online',
    }).item;

    render(<ContainerInstancesModal bot={bot} userId="user-1" canRestart={false} onClose={jest.fn()} />);

    await waitFor(() =>
      expect(getSummaryText('共 2 个实例')).toBeInTheDocument(),
    );
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(getSummaryText('1 活跃')).toBeInTheDocument();
    expect(getSummaryText('1 异常')).toBeInTheDocument();
    expect(screen.getByText('实例名称')).toBeInTheDocument();
    expect(screen.getByText('device-2')).toBeInTheDocument();
    expect(screen.queryByText('node-a')).not.toBeInTheDocument();
    expect(screen.queryByText('CPU')).not.toBeInTheDocument();
    expect(screen.queryByText('内存')).not.toBeInTheDocument();
    const restartButtons = screen.getAllByRole('button', { name: '重启' });
    expect(restartButtons).toHaveLength(2);
    restartButtons.forEach((button) => expect(button).toBeDisabled());
  });

  it('Owner 可提交负数差值扩缩容并展示审批状态', async () => {
    list.mockResolvedValue({
      botId: 'service-1',
      summary: { total: 2, healthy: 2, abnormal: 0, restarting: 0, unknown: 0 },
      instances: [
        { id: 'device-1', engine: 'openclaw', status: 'healthy' },
        { id: 'device-2', engine: 'openclaw', status: 'healthy' },
      ],
    });
    scale.mockResolvedValue({
      botId: 'service-1',
      accepted: true,
      currentCount: 2,
      delta: -1,
      targetCount: 1,
      approval: {
        status: 'PROCESSING',
        approvalUrl: 'https://approval.example/scale-1',
        currentCount: 2,
        delta: -1,
        targetCount: 1,
      },
    });
    const bot = mapBotDto({
      bot_id: 'service-1',
      bot_name: '服务 Bot',
      bot_type: 'service',
      engine: 'openclaw',
      owner_id: 'owner-1',
      display_state: 'service_online',
    }).item;

    render(<ContainerInstancesModal bot={bot} userId="user-1" canRestart onClose={jest.fn()} />);

    await waitFor(() => expect(getSummaryText('共 2 个实例')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '扩缩容' }));
    expect(screen.getByText('当前实例数')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('扩容数量'), { target: { value: '-5' } });
    expect(screen.getByRole('button', { name: '提交审批' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('扩容数量'), { target: { value: '-1' } });
    expect(screen.getByText(/审批通过后目标为 1 个/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '提交审批' }));

    await waitFor(() => expect(scale).toHaveBeenCalledWith('service-1', -1, 'user-1', 'owner-1'));
    expect(await screen.findByText('状态：审批中；目标 1 个实例')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看审批' })).toBeInTheDocument();
  });

  it('不渲染非 http(s) 审批链接', async () => {
    list.mockResolvedValue({
      botId: 'service-1',
      summary: { total: 1, healthy: 1, abnormal: 0, restarting: 0, unknown: 0 },
      instances: [{ id: 'device-1', engine: 'openclaw', status: 'healthy' }],
      scaleApproval: { status: 'PROCESSING', approvalUrl: 'javascript:alert(1)' },
    });
    const bot = mapBotDto({
      bot_id: 'service-1', bot_name: '服务 Bot', bot_type: 'service', engine: 'openclaw',
      owner_id: 'owner-1', display_state: 'service_online',
    }).item;

    render(<ContainerInstancesModal bot={bot} userId="user-1" canRestart onClose={jest.fn()} />);

    await waitFor(() => expect(getSummaryText('共 1 个实例')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '扩缩容' }));
    expect(screen.queryByRole('button', { name: '查看审批' })).toBeNull();
  });

  it('非 Owner 不展示扩缩容页签', async () => {
    list.mockResolvedValue({
      botId: 'service-1',
      summary: { total: 1, healthy: 1, abnormal: 0, restarting: 0, unknown: 0 },
      instances: [{ id: 'device-1', engine: 'openclaw', status: 'healthy' }],
    });
    const bot = mapBotDto({
      bot_id: 'service-1',
      bot_name: '服务 Bot',
      bot_type: 'service',
      engine: 'openclaw',
      display_state: 'service_online',
    }).item;

    render(<ContainerInstancesModal bot={bot} userId="user-1" canRestart={false} onClose={jest.fn()} />);

    await waitFor(() => expect(getSummaryText('共 1 个实例')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: '扩缩容' })).toBeNull();
  });
});
