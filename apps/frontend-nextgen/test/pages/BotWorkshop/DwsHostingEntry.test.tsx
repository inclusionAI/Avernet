/** @jest-environment jsdom */
import Page from '@/pages/BotWorkshop/Detail';
import { dwsHostingService } from '@/services/botWorkshop/dwsHostingService';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen } from '@testing-library/react';

jest.mock('@umijs/max', () => ({ useLocation: () => ({ search: '?id=b&type=edit' }) }));
jest.mock('@/hooks/useBotWorkshopEditorIdentity', () => ({
  useBotWorkshopEditorIdentity: () => ({ ready: true, userId: 'u' }),
}));
jest.mock('@/hooks/useBotWorkshopDetail', () => ({
  useBotWorkshopDetail: () => ({
    bot: {
      id: 'b',
      ownerId: 'u',
      serviceMode: 'non-service',
      deployment: 'cloud',
      runtime: { engine: 'openclaw' },
    },
  }),
}));
jest.mock('@/hooks/useBotEditor', () => ({
  useBotEditor: () => ({ skills: [], skillSets: [] }),
}));
jest.mock('@/hooks/useBotAdvancedConfig', () => ({ useBotAdvancedConfig: () => ({}) }));
jest.mock('@/hooks/useDesktopFolder', () => ({ useDesktopFolder: () => jest.fn() }));
jest.mock('@/hooks/useSpaceContext', () => ({ useSpaceContext: () => undefined }));
jest.mock('@/services/botWorkshop/dwsHostingService', () => ({
  dwsHostingService: { eligibility: jest.fn(), status: jest.fn() },
}));
jest.mock('@/pages/BotWorkshop/Detail/DetailHeader', () => ({ DetailHeader: () => null }));
jest.mock('@/components/BotWorkshop/TaskEscort', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/BotWorkshop/Editor/CapabilityPanel', () => ({ CapabilityPanel: () => null }));
jest.mock('@/components/BotWorkshop/Editor/ChannelConfigPanel', () => ({ ChannelConfigPanel: () => null }));
jest.mock('@/components/BotWorkshop/Editor/DebugChatPanel', () => ({ DebugChatPanel: () => null }));
jest.mock('@/components/BotWorkshop/Editor/IdentityConfigPanel', () => ({ IdentityConfigPanel: () => null }));
jest.mock('@/components/BotWorkshop/Editor/LinkPanel', () => ({ LinkPanel: () => null }));
jest.mock('@/components/BotWorkshop/Editor/MoreConfigPanel', () => ({ MoreConfigPanel: () => null }));
jest.mock('@/components/BotWorkshop/Editor/RenderScreenPanel', () => ({ RenderScreenPanel: () => null }));
jest.mock('@/components/BotWorkshop/Editor/ResourcePanel', () => ({ ResourcePanel: () => null }));
jest.mock('@/components/BotWorkshop/Editor/RoutinePanel', () => ({ RoutinePanel: () => null }));
jest.mock('@/components/BotWorkshop/Editor/DwsHostingPanel', () => ({
  DwsHostingPanel: () => <div>托管设置面板</div>,
}));
const eligibility = jest.mocked(dwsHostingService.eligibility);
beforeEach(() => {
  jest.useFakeTimers();
  eligibility.mockReset();
  jest
    .mocked(dwsHostingService.status)
    .mockReset()
    .mockResolvedValue({
      enabled: false,
      running: false,
      intake_ready: false,
      error: '',
      authorization: 'required',
      challenge: null,
      unknown_executions: 0,
      unknown_deliveries: 0,
    });
});

test('gray user with unavailable engine never sees the entry', async () => {
  eligibility.mockResolvedValue(true);
  jest.mocked(dwsHostingService.status).mockRejectedValue(new Error('unsupported'));
  render(<Page />);
  await act(async () => {
    await Promise.resolve();
  });
  expect(screen.queryByRole('button', { name: '消息托管' })).not.toBeInTheDocument();
});
afterEach(() => jest.useRealTimers());

test('non-gray users never see the entry or mount the hosting panel', async () => {
  eligibility.mockResolvedValue(false);
  render(<Page />);
  await act(async () => {
    await Promise.resolve();
  });
  expect(screen.queryByRole('button', { name: '消息托管' })).not.toBeInTheDocument();
  expect(screen.queryByText('托管设置面板')).not.toBeInTheDocument();
});

test('positive gray grant exposes entry; revocation removes both entry and active panel', async () => {
  eligibility.mockResolvedValue(true);
  render(<Page />);
  expect(screen.queryByRole('button', { name: '消息托管' })).not.toBeInTheDocument();
  await act(async () => {
    await Promise.resolve();
  });
  fireEvent.click(screen.getByRole('button', { name: '消息托管' }));
  expect(screen.getByText('托管设置面板')).toBeInTheDocument();
  eligibility.mockResolvedValue(false);
  await act(async () => {
    jest.advanceTimersByTime(30000);
  });
  expect(screen.queryByRole('button', { name: '消息托管' })).not.toBeInTheDocument();
  expect(screen.queryByText('托管设置面板')).not.toBeInTheDocument();
});
