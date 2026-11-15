/** @jest-environment jsdom */
import { useBotSessionFiles } from '@/pages/Workspace/hooks/useBotSessionFiles';
import { useBotSessionFilesFeature } from '@/pages/Workspace/hooks/useBotSessionFilesFeature';
import { useBotSessionFileUpload } from '@/pages/Workspace/hooks/useBotSessionFileUpload';
import { useBotSessionFilePreview } from '@/pages/Workspace/hooks/useBotSessionFilePreview';
import { useBotSkills } from '@/pages/Workspace/hooks/useBotSkills';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import { fireEvent, render, renderHook, screen } from '@testing-library/react';

// 资源面板跳转入口引入 history：jsdom 下真实 @umijs/max 会拉起 esbuild 触发
// Buffer invariant 失败，按项目既有惯例用轻量桩替换（见 appRuntime.test.ts 等）。
jest.mock('@umijs/max', () => ({ history: { push: () => undefined, replace: () => undefined } }));
jest.mock('@/pages/Workspace/hooks/useBotSessionFiles');
jest.mock('@/pages/Workspace/hooks/useBotSessionFileUpload');
jest.mock('@/pages/Workspace/hooks/useBotSkills');
jest.mock('sonner');
jest.mock('@/pages/Workspace/hooks/useBotSessionFilePreview');

const mockedUseBotSessionFiles = useBotSessionFiles as jest.Mock;
const mockedUseBotSessionFileUpload = useBotSessionFileUpload as jest.Mock;
const mockedUseBotSkills = useBotSkills as jest.Mock;

const bot: ChatBotView = {
  botId: 'bot:2088',
  realBotId: 'bot',
  ownerId: '2088',
  displayName: 'Bot',
  online: true,
  chatable: true,
};
const session: BotChatSessionView = {
  sessionId: 'session-1',
  botId: bot.botId,
  title: 'Session',
  messageCount: 0,
  gmtModified: '',
  gmtCreate: '',
};

beforeEach(() => {
  jest.mocked(useBotSessionFilePreview).mockReturnValue({ kind: 'other', status: 'unsupported', contentUrl: null, text: null, truncated: false, errorMessage: null });
  mockedUseBotSessionFiles.mockReturnValue({
    readyFiles: [],
    isLoadingList: false,
    refresh: jest.fn(),
    deleteFile: jest.fn(),
    downloadFile: jest.fn(),
  });
  mockedUseBotSessionFileUpload.mockReturnValue({
    tasks: [],
    isUploading: false,
    submit: jest.fn(),
    removeTask: jest.fn(),
    cancelAll: jest.fn(),
  });
  mockedUseBotSkills.mockReturnValue({ skills: [], isLoading: false, loadSkills: jest.fn() });
});

it('托管 Bot 的 / 命令保留 skill 入口', () => {
  const { result } = renderHook(() => useBotSessionFilesFeature(bot, session, 'human_900003', jest.fn()));

  const items = result.current.command.categories[0]?.items ?? [];
  expect(items.map((item) => item.name)).toContain('skill');
  expect(mockedUseBotSessionFiles).toHaveBeenLastCalledWith('bot', 'session-1', 'human_900003', '2088', undefined);
});

it('好友 Bot 的 / 命令不展示 skill 入口', () => {
  const { result } = renderHook(() =>
    useBotSessionFilesFeature({ ...bot, isFriendBot: true }, session, 'human_900003', jest.fn()),
  );

  const items = result.current.command.categories[0]?.items ?? [];
  expect(items.map((item) => item.name)).toEqual(['file', 'clear']);
  expect(mockedUseBotSessionFiles).toHaveBeenLastCalledWith('bot', 'session-1', 'human_900003', '2088', 'human_900003');
});

it('对话资源面板点击外部收起，入口再次点击不会先关闭又打开', () => {
  function Fixture() {
    const files = useBotSessionFilesFeature(bot, session, 'human_900003', jest.fn());
    return (
      <>
        <button
          type="button"
          data-workspace-panel-trigger
          onClick={files.fileDrawerOpen ? files.closeFileDrawer : files.openFileDrawer}
        >
          资源入口
        </button>
        <button type="button">聊天消息</button>
        {files.featureNode}
      </>
    );
  }
  render(<Fixture />);
  const trigger = screen.getByText('资源入口');
  fireEvent.click(trigger);
  // 内联副屏常挂：展开/收起用宽度表达，关闭不再卸载容器。
  const drawer = screen.getByLabelText('资源管理面板');
  expect(drawer).toHaveStyle({ width: '380px' });
  fireEvent.pointerDown(trigger);
  fireEvent.click(trigger);
  expect(drawer).toHaveStyle({ width: '0px' });
  expect(drawer).toHaveClass('opacity-0', 'pointer-events-none');
  fireEvent.click(trigger);
  expect(drawer).toHaveStyle({ width: '380px' });
  fireEvent.pointerDown(screen.getByText('聊天消息'));
  expect(drawer).toHaveStyle({ width: '0px' });
});

it.each([
  ['team service', { isTeamBot: true, botType: 'service' }, 'online'],
  ['friend service', { isFriendBot: true, botType: 'service' }, 'online'],
  ['team personal', { isTeamBot: true, botType: 'personal' }, undefined],
  ['friend personal', { isFriendBot: true, botType: 'personal' }, undefined],
  ['my online service', { botType: 'service', runtimeStage: 'online' as const }, 'online'],
  ['my personal', { botType: 'personal', runtimeStage: 'draft' as const }, undefined],
  ['my service', { botType: 'service', runtimeStage: 'draft' as const }, undefined],
])('%s files and uploads use the selected conversation stage', (_label, override, stage) => {
  const target = { ...bot, ...override };
  renderHook(() => useBotSessionFilesFeature(target, session, 'human_900003', jest.fn()));
  expect(mockedUseBotSessionFiles.mock.calls.at(-1)).toEqual([
    'bot',
    'session-1',
    'human_900003',
    '2088',
    target.isFriendBot ? 'human_900003' : undefined,
    ...(stage ? [stage] : []),
  ]);
  expect(mockedUseBotSessionFileUpload.mock.calls.at(-1)).toEqual([
    'bot',
    'session-1',
    'human_900003',
    '2088',
    ...(stage ? [stage] : []),
  ]);
});

it('团队服务 Bot 资源面板将 online 阶段传递给预览 Hook', () => {
  function Fixture() {
    const files = useBotSessionFilesFeature(
      { ...bot, isTeamBot: true, botType: 'service' },
      session,
      'viewer',
      jest.fn(),
    );
    return (
      <>
        <button type="button" onClick={files.openFileDrawer}>Open files</button>
        {files.featureNode}
      </>
    );
  }
  render(<Fixture />);
  fireEvent.click(screen.getByText('Open files'));
  expect(useBotSessionFilePreview).toHaveBeenLastCalledWith(null, expect.objectContaining({ stage: 'online' }));
});

it.each(['draft', 'verify', 'online'] as const)('debug stage override %s applies to file list, uploads and preview', (stage) => {
  function Fixture() {
    const files = useBotSessionFilesFeature({ ...bot, botType: 'service', runtimeStage: stage }, session, 'viewer', jest.fn(), stage);
    return <><button type="button" onClick={files.openFileDrawer}>Open debug files</button>{files.featureNode}</>;
  }
  render(<Fixture />);
  fireEvent.click(screen.getByText('Open debug files'));
  expect(mockedUseBotSessionFiles).toHaveBeenLastCalledWith('bot', 'session-1', 'viewer', '2088', undefined, stage);
  expect(mockedUseBotSessionFileUpload).toHaveBeenLastCalledWith('bot', 'session-1', 'viewer', '2088', stage);
  expect(useBotSessionFilePreview).toHaveBeenLastCalledWith(null, expect.objectContaining({ stage }));
});
