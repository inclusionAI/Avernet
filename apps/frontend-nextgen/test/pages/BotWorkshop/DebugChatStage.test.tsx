/** @jest-environment jsdom */
import Page from '@/pages/BotWorkshop/Detail';
import { useBotChat } from '@/pages/Workspace/hooks/useBotChat';
import { useBotSessionFilesFeature } from '@/pages/Workspace/hooks/useBotSessionFilesFeature';
import { botSessionService } from '@/services/workspace/botSessionService';
import type { BotLifecycle, BotRuntimeStage } from '@/domain/botWorkshop';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

let mockSearch = '';
let mockLifecycle: BotLifecycle = 'running';
jest.mock('@umijs/max', () => ({ useLocation: () => ({ search: mockSearch }) }));
jest.mock('@/hooks/useBotWorkshopEditorIdentity', () => ({
  useBotWorkshopEditorIdentity: () => ({ ready: true, userId: 'viewer' }),
}));
jest.mock('@/hooks/useBotWorkshopDetail', () => ({ useBotWorkshopDetail: () => ({ bot: {
  id: 'bot', name: 'Service', ownerId: 'owner', serviceMode: 'service',
  lifecycle: mockLifecycle, deployment: 'cloud', runtime: { engine: 'openclaw' },
} }) }));
jest.mock('@/hooks/useBotEditor', () => ({ useBotEditor: () => ({}) }));
jest.mock('@/hooks/useBotAdvancedConfig', () => ({ useBotAdvancedConfig: () => ({}) }));
jest.mock('@/hooks/useDesktopFolder', () => ({ useDesktopFolder: () => jest.fn() }));
jest.mock('@/hooks/useSpaceContext', () => ({ useSpaceContext: () => undefined }));
jest.mock('@/pages/BotWorkshop/Detail/useBotEditorPanels', () => ({
  useBotEditorPanels: () => ({ panels: [], additionalTabs: [] }),
}));
jest.mock('@/pages/BotWorkshop/Detail/useHostingTabs', () => ({ useHostingTabs: () => ({ visibleMainTabs: [] }) }));
jest.mock('@/pages/BotWorkshop/Detail/DetailHeader', () => ({ DetailHeader: () => null }));
jest.mock('@/pages/BotWorkshop/Detail/DetailNavigation', () => ({ DetailNavigation: () => null }));
jest.mock('@/pages/BotWorkshop/Detail/DetailPanelContent', () => ({ DetailPanelContent: () => null }));
jest.mock('@/pages/Workspace/hooks/useBotChat');
jest.mock('@/pages/Workspace/hooks/useBotSessionFilesFeature', () => ({
  useBotSessionFilesFeature: jest.fn(() => ({ capabilitySets: null, featureNode: null, cancelUploads: jest.fn() })),
}));
jest.mock('@/pages/Workspace/hooks/useBotCapabilitySets', () => ({ useBotCapabilitySets: () => null }));
jest.mock('@/pages/Workspace/hooks/useBotModels', () => ({ useBotModels: () => ({ models: [], loading: false }) }));
jest.mock('@/services/workspace/botSessionService', () => ({
  ...jest.requireActual('@/services/workspace/botSessionService'),
  botSessionService: { listSessions: jest.fn(), listModels: jest.fn() },
}));
jest.mock('@/components/BotWorkshop/Editor/DebugChatComposer', () => ({
  DebugChatComposer: () => <div data-testid="composer" />,
}));
beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(botSessionService.listSessions).mockResolvedValue({ ok: true, data: [] });
  jest.mocked(useBotChat).mockReturnValue({
    chat: { messages: [], isRequesting: false }, connectionStatus: 'disconnected', send: jest.fn(), stop: jest.fn(),
  } as unknown as ReturnType<typeof useBotChat>);
});
it.each<[string, BotLifecycle, BotRuntimeStage | undefined, BotRuntimeStage]>([
  ['edit', 'running', 'online', 'draft'],
  ['edit', 'running', undefined, 'draft'],
  ['edit', 'draft', 'draft', 'draft'],
  ['edit', 'running', 'verify', 'verify'],
  ['edit', 'prestable', 'online', 'verify'],
  ['view', 'running', 'online', 'online'],
  ['view', 'running', undefined, 'online'],
  ['view', 'prestable', 'verify', 'verify'],
])('%s lifecycle=%s route=%s uses %s for debug requests and file feature', async (mode, lifecycle, stage, expected) => {
  mockLifecycle = lifecycle;
  mockSearch = `?id=bot&type=${mode}${stage ? `&runtime_stage=${stage}` : ''}`;
  render(<Page />);
  await waitFor(() => expect(botSessionService.listSessions).toHaveBeenCalledWith(
    expect.objectContaining({ runtimeStage: expected, ownerId: 'owner' }), 'viewer',
  ));
  expect(useBotChat).toHaveBeenLastCalledWith(expect.objectContaining({ runtimeStage: expected }),
    null, undefined, expect.any(Function), 'viewer');
  expect(screen.getByTestId('composer')).toBeInTheDocument();
  expect(useBotSessionFilesFeature).toHaveBeenLastCalledWith(
    expect.objectContaining({ runtimeStage: expected, ownerId: 'owner' }), null, 'viewer', expect.any(Function), expected,
  );
});
