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

  it('renders the live instance summary and does not invent absent metrics', async () => {
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

    render(<ContainerInstancesModal bot={bot} canRestart={false} onClose={jest.fn()} />);

    await waitFor(() =>
      expect(getSummaryText('共 2 个实例')).toBeInTheDocument(),
    );
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(getSummaryText('1 活跃')).toBeInTheDocument();
    expect(getSummaryText('1 异常')).toBeInTheDocument();
    expect(screen.getByText('node-a')).toBeInTheDocument();
    expect(screen.getAllByText('—')).toHaveLength(2);
    expect(screen.getByRole('button', { name: '重启' })).toBeDisabled();
  });
});
