/** @jest-environment jsdom */
import { ExternalBotPanel } from '@/components/BotWorkshop/ExternalBotPanel';
import { externalBotService } from '@/services/botWorkshop/externalBotService';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/services/botWorkshop/externalBotService', () => ({
  ...jest.requireActual('@/services/botWorkshop/externalBotService'),
  externalBotService: { list: jest.fn() },
}));
jest.mock('@/components/CollaborationPrivacy/BotGeneralConfigDialog', () => ({
  BotGeneralConfigDialog: ({ isExternalBot }: { isExternalBot?: boolean }) => (
    <div>{isExternalBot ? '外部 Bot 配置' : 'TC Bot 配置'}</div>
  ),
}));
jest.mock('@/components/Workspace/IdentitySelector/BotRegistrationDialog', () => ({
  BotRegistrationDialog: ({ open, onClose }: { open: boolean; onClose: () => void }) =>
    open ? (
      <button type="button" onClick={onClose}>
        关闭接入指南
      </button>
    ) : null,
}));

afterEach(() => jest.resetAllMocks());

it('外部 Bot 的通用配置入口明确使用外部配置模式', async () => {
  jest.mocked(externalBotService.list).mockResolvedValue([{ id: 'external-1', name: '外部助手' }]);
  render(<ExternalBotPanel active />);
  fireEvent.click(await screen.findByRole('button', { name: '通用配置' }));
  expect(screen.getByText('外部 Bot 配置')).toBeInTheDocument();
});

it('关闭接入指南后重新查询并显示刚接入的外部 Bot', async () => {
  jest
    .mocked(externalBotService.list)
    .mockResolvedValueOnce([])
    .mockResolvedValueOnce([{ id: 'qwen-1', name: '我的千问办公', status: 'online', reachability: 'reachable' }]);
  render(<ExternalBotPanel active />);
  await screen.findByText('暂无已接入的外部 Bot');
  fireEvent.click(screen.getAllByRole('button', { name: '接入外部 Bot' })[0]);
  expect(externalBotService.list).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: '关闭接入指南' }));

  await screen.findByText('我的千问办公');
  expect(screen.getByText('在线')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '关闭接入指南' })).not.toBeInTheDocument();
  expect(externalBotService.list).toHaveBeenCalledTimes(2);
});

it('关闭接入指南后更新已有 Bot 的连接状态并保留搜索词', async () => {
  const bot = { id: 'qwen-1', name: '我的千问办公' };
  jest
    .mocked(externalBotService.list)
    .mockResolvedValueOnce([{ ...bot, status: 'offline' }])
    .mockResolvedValueOnce([{ ...bot, status: 'online', reachability: 'reachable' }]);
  render(<ExternalBotPanel active />);
  await screen.findByText('离线');
  fireEvent.change(screen.getByRole('textbox', { name: '搜索外部 Bot' }), { target: { value: '千问' } });
  fireEvent.click(screen.getByRole('button', { name: '接入外部 Bot' }));
  fireEvent.click(screen.getByRole('button', { name: '关闭接入指南' }));

  await waitFor(() => expect(screen.getByText('在线')).toBeInTheDocument());
  expect(screen.queryByText('离线')).not.toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: '搜索外部 Bot' })).toHaveValue('千问');
  expect(externalBotService.list).toHaveBeenCalledTimes(2);
});
