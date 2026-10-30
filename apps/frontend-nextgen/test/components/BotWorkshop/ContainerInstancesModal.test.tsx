/** @jest-environment jsdom */

import { ContainerInstancesModal } from '@/components/BotWorkshop/ContainerInstancesModal';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import { botContainerService } from '@/services/botWorkshop/botContainerService';
import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';

jest.mock('@/services/botWorkshop/botContainerService', () => ({
  botContainerService: { list: jest.fn(), restart: jest.fn() },
}));

const list = botContainerService.list as jest.Mock;
const getSummaryText = (value: string) =>
  screen.getByText((_, element) => element?.textContent === value);

describe('ContainerInstancesModal', () => {
  beforeEach(() => {
    list.mockReset();
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
});
