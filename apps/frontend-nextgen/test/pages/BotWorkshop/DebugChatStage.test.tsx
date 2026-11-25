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
let mockComposerDisabled: boolean | undefined;
let mockComposerPlaceholder: string | undefined;
jest.mock('@/components/BotWorkshop/Editor/DebugChatComposer', () => ({
  DebugChatComposer: (props: Record<string, unknown>) => {
    mockComposerDisabled = props.composerDisabled as boolean | undefined;
    mockComposerPlaceholder = props.composerDisabledPlaceholder as string | undefined;
    return <div data-testid="composer" />;
  },
}));
beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(botSessionService.listSessions).mockResolvedValue({ ok: true, data: [{
    sessionId: 'debug-session', botId: 'bot', title: 'Debug', messageCount: 0, gmtCreate: '', gmtModified: '',
  }] });
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
    expect.objectContaining({ runtimeStage: expected, ownerId: 'owner' }), 'viewer', false,
  ));
  expect(await screen.findByTestId('composer')).toBeInTheDocument();
  expect(useBotChat).toHaveBeenLastCalledWith(expect.objectContaining({ runtimeStage: expected }),
    expect.objectContaining({ sessionId: 'debug-session' }), undefined, expect.any(Function), 'viewer',
    { subjectScoped: false });
  expect(useBotSessionFilesFeature).toHaveBeenLastCalledWith(
    expect.objectContaining({ runtimeStage: expected, ownerId: 'owner' }),
    expect.objectContaining({ sessionId: 'debug-session' }),
    'viewer',
    expect.any(Function),
    expected,
    { subjectScoped: false },
  );
});

it('查看 online 运行态时全面禁用调试输入并提示改走对话页', async () => {
  mockLifecycle = 'running';
  mockSearch = '?id=bot&type=view&runtime_stage=online';
  render(<Page />);
  expect(await screen.findByTestId('composer')).toBeInTheDocument();
  expect(mockComposerDisabled).toBe(true);
  expect(mockComposerPlaceholder).toBe('正式运行的服务Bot请在“对话”中使用');
});

it('编辑模式与线上路由组合不触发调试输入禁用', async () => {
  mockLifecycle = 'running';
  mockSearch = '?id=bot&type=edit&runtime_stage=online';
  render(<Page />);
  expect(await screen.findByTestId('composer')).toBeInTheDocument();
  expect(mockComposerDisabled).toBe(false);
});

it('查看模式下预发(prestable)不显示锁定横幅,配置面板可浏览不整页禁用', async () => {
  mockLifecycle = 'prestable';
  mockSearch = '?id=bot&type=view&runtime_stage=verify';
  render(<Page />);
  expect(await screen.findByTestId('composer')).toBeInTheDocument();
  expect(screen.queryByText('试运行验证中，配置已锁定；请在右侧对话区验证 Bot。')).not.toBeInTheDocument();
  const fieldset = document.querySelector('fieldset');
  expect(fieldset).not.toBeNull();
  expect((fieldset as HTMLFieldSetElement).disabled).toBe(false);
  expect(fieldset?.className).not.toContain('pointer-events-none');
});

it('编辑模式下预发(prestable)仍显示锁定横幅并整页禁用', async () => {
  mockLifecycle = 'prestable';
  mockSearch = '?id=bot&type=edit&runtime_stage=verify';
  render(<Page />);
  expect(await screen.findByText('试运行验证中，配置已锁定；请在右侧对话区验证 Bot。')).toBeInTheDocument();
  const fieldset = document.querySelector('fieldset') as HTMLFieldSetElement;
  expect(fieldset.disabled).toBe(true);
  expect(fieldset.className).toContain('pointer-events-none');
});
