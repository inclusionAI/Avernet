/** @jest-environment jsdom */

import { BotContainerInfo } from '@/components/BotWorkshop/BotCard/BotContainerInfo';
import { mapBotDto } from '@/services/botWorkshop/botMapper';
import { botContainerService } from '@/services/botWorkshop/botContainerService';
import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';

jest.mock('@/services/botWorkshop/botContainerService', () => ({
  botContainerService: { list: jest.fn() },
}));

const list = botContainerService.list as jest.Mock;
const serviceBot = mapBotDto({
  bot_id: 'service-1',
  bot_name: '服务 Bot',
  bot_type: 'service',
  kind: 'service',
  engine: 'openclaw',
  status: 'ACTIVE',
  publish: { status: 'success' },
}).item;

beforeEach(() => {
  list.mockReset();
});

test('Teclaw 运行态服务 Bot 不展示容器入口且不请求快照', () => {
  const bot = { ...serviceBot, runtime: { ...serviceBot.runtime, engine: 'teclaw' as const } };
  render(<BotContainerInfo bot={bot} userId="user-1" canView onOpen={jest.fn()} />);
  expect(list).not.toHaveBeenCalled();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});

test('无 Owner/协作者资格时不预取容器，避免公共空间 404', async () => {
  render(<BotContainerInfo bot={serviceBot} userId="user-1" canView={false} onOpen={jest.fn()} />);

  expect(screen.getByText('—')).toBeInTheDocument();
  await waitFor(() => expect(list).not.toHaveBeenCalled());
});

test('请求身份未就绪时不发送无 user_id 的容器请求', async () => {
  render(<BotContainerInfo bot={serviceBot} canView onOpen={jest.fn()} />);

  expect(screen.getByText('—')).toBeInTheDocument();
  await waitFor(() => expect(list).not.toHaveBeenCalled());
});

test('具备资格的运行中服务 Bot 仍预取容器摘要', async () => {
  list.mockResolvedValue({
    botId: 'service-1',
    summary: { total: 1, healthy: 1, abnormal: 0, restarting: 0, unknown: 0 },
    instances: [],
  });
  render(<BotContainerInfo bot={serviceBot} userId="user-1" canView onOpen={jest.fn()} />);

  await waitFor(() => expect(list).toHaveBeenCalledWith('service-1', 'user-1'));
  expect(screen.getByText('1/1')).toBeInTheDocument();
});
