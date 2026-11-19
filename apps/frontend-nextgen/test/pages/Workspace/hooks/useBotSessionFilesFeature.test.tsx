/** @jest-environment jsdom */
import { useBotSessionFiles } from '@/pages/Workspace/hooks/useBotSessionFiles';
import { useBotSessionFilesFeature } from '@/pages/Workspace/hooks/useBotSessionFilesFeature';
import { useBotSessionFileUpload } from '@/pages/Workspace/hooks/useBotSessionFileUpload';
import { useBotSessionFilePreview } from '@/pages/Workspace/hooks/useBotSessionFilePreview';
import { useBotSkills } from '@/pages/Workspace/hooks/useBotSkills';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';

// 资源面板跳转入口引入 history：jsdom 下真实 @umijs/max 会拉起 esbuild 触发
// Buffer invariant 失败，按项目既有惯例用轻量桩替换（见 appRuntime.test.ts 等）。
jest.mock('@umijs/max', () => ({ history: { push: () => undefined, replace: () => undefined } }));
jest.mock('@/pages/Workspace/hooks/useBotSessionFiles');
jest.mock('@/pages/Workspace/hooks/useBotSessionFileUpload');
jest.mock('@/pages/Workspace/hooks/useBotSkills');
jest.mock('sonner');
jest.mock('@/pages/Workspace/hooks/useBotSessionFilePreview');

const mockGetCallerContext = jest.fn();

jest.mock('@/services/botWorkshop/botEditorService', () => ({
  botEditorService: { getCallerContext: (...args: unknown[]) => mockGetCallerContext(...(args as [])) },
}));

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
  // 探测默认非 Caller(openspec add-session-files-caller-runtime-target D6),既有断言的调用形态不受探测影响
  mockGetCallerContext.mockReset();
  mockGetCallerContext.mockResolvedValue({
    editable: false,
    botCallType: 'owner',
    mcpCallTypes: {},
    cliCallTypes: {},
  });
});

it('托管 Bot 的 / 命令保留 skill 入口', () => {
  const { result } = renderHook(() => useBotSessionFilesFeature(bot, session, 'human_900003', jest.fn()));

  const items = result.current.command.categories[0]?.items ?? [];
  expect(items.map((item) => item.name)).toContain('skill');
  expect(mockedUseBotSessionFiles).toHaveBeenLastCalledWith('bot', 'session-1', 'human_900003', '2088', undefined, undefined, undefined, undefined);
});

it('好友 Bot 的 / 命令不展示 skill 入口', () => {
  const { result } = renderHook(() =>
    useBotSessionFilesFeature({ ...bot, isFriendBot: true }, session, 'human_900003', jest.fn()),
  );

  const items = result.current.command.categories[0]?.items ?? [];
  expect(items.map((item) => item.name)).toEqual(['file', 'clear']);
  expect(mockedUseBotSessionFiles).toHaveBeenLastCalledWith('bot', 'session-1', 'human_900003', '2088', 'human_900003', undefined, undefined, undefined);
});

it.each([false, true])('桌面 Bot（团队协作=%s）可懒加载技能，但不加载会话文件', async (isTeamBot) => {
  const loadSkills = jest.fn(async () => [
    { skillId: 's1', name: 'task-loop', active: true, description: '执行任务', category: 'general' },
  ]);
  mockedUseBotSkills.mockReturnValue({ skills: [], isLoading: false, loadSkills });
  const { result } = renderHook(() => useBotSessionFilesFeature(
    { ...bot, botType: 'desktop', isTeamBot }, session, 'human_900003', jest.fn(),
  ));
  const skillEntry = () => result.current.command.categories[0].items!.find((item) => item.name === 'skill')!;
  expect(loadSkills).not.toHaveBeenCalled();
  await act(async () => { result.current.command.onSelect?.(skillEntry()); });
  expect(loadSkills).toHaveBeenCalledTimes(1);
  expect(skillEntry().subConfig!.categories[0].items?.map((item) => item.name)).toEqual(['task-loop']);
  expect(skillEntry().subConfig!.format?.({ id: 's1', name: 'task-loop' })).toBe('/task-loop');
  expect(result.current.command.categories[0].items?.some((item) => item.name === 'file')).toBe(false);
  expect(jest.mocked(useBotSessionFiles).mock.results.at(-1)!.value.refresh).not.toHaveBeenCalled();
  expect(mockedUseBotSessionFiles.mock.calls.at(-1)?.[0]).toBeNull();
  expect(mockedUseBotSessionFileUpload.mock.calls.at(-1)?.[0]).toBeNull();
});

it.each(['personal', 'service'])('Teclaw %s 禁用所有会话文件入口，但保留 Skill 与能力集', async (botType) => {
  const loadSkills = jest.fn(async () => []);
  mockedUseBotSkills.mockReturnValue({ skills: [], isLoading: false, loadSkills });
  const { result } = renderHook(() => useBotSessionFilesFeature(
    { ...bot, botType, engine: 'teclaw' }, session, 'human_900003', jest.fn(),
  ));
  expect(result.current.filesSupported).toBe(false);
  expect(result.current.featureNode).toBeNull();
  expect(result.current.capabilitySets).not.toBeNull();
  expect(result.current.command.categories[0].items?.map((item) => item.name)).toEqual(['skill', 'clear']);
  await act(async () => {
    result.current.openFileDrawer();
    result.current.openUpload();
    result.current.fileChip.onPick(jest.fn(), jest.fn());
    result.current.command.onSelect?.(result.current.command.categories[0].items![0]);
  });
  expect(loadSkills).toHaveBeenCalledTimes(1);
  expect(result.current.fileDrawerOpen).toBe(false);
  expect(mockedUseBotSessionFiles.mock.calls.at(-1)?.[0]).toBeNull();
  expect(mockedUseBotSessionFileUpload.mock.calls.at(-1)?.[0]).toBeNull();
  expect(jest.mocked(useBotSessionFiles).mock.results.at(-1)!.value.refresh).not.toHaveBeenCalled();
});

it.each([
  { chatable: false, isFriendBot: false },
  { chatable: true, isFriendBot: true },
])('桌面 Bot 不具备技能访问资格时不装配命令：%j', async (access) => {
  const { result } = renderHook(() => useBotSessionFilesFeature(
    { ...bot, botType: 'desktop', ...access }, session, 'human_900003', jest.fn(),
  ));
  expect(result.current.command.categories[0].items?.some((item) => item.name === 'skill')).toBe(false);
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
    stage ?? undefined,
    undefined,
    undefined,
  ]);
  expect(mockedUseBotSessionFileUpload.mock.calls.at(-1)).toEqual([
    'bot',
    'session-1',
    'human_900003',
    '2088',
    stage ?? undefined,
    undefined,
    undefined,
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
  expect(mockedUseBotSessionFiles).toHaveBeenLastCalledWith('bot', 'session-1', 'viewer', '2088', undefined, stage, undefined, undefined);
  expect(mockedUseBotSessionFileUpload).toHaveBeenLastCalledWith('bot', 'session-1', 'viewer', '2088', stage, undefined, undefined);
  expect(useBotSessionFilePreview).toHaveBeenLastCalledWith(null, expect.objectContaining({ stage }));
});

// ── Caller 判据经 caller-context 运行时接口(openspec add-session-files-caller-runtime-target D6)──

it('探测到 bot_call_type=caller 时服务 Bot 注入 callerRuntimeTarget=service', async () => {
  mockGetCallerContext.mockResolvedValue({
    editable: false,
    botCallType: 'caller',
    mcpCallTypes: {},
    cliCallTypes: {},
  });
  renderHook(() => useBotSessionFilesFeature({ ...bot, botType: 'service' }, session, 'viewer', jest.fn()));
  await waitFor(() => {
    expect(mockGetCallerContext).toHaveBeenCalledWith('bot', '2088');
  });
  await waitFor(() => {
    // files 链位参:botId, sessionId, userId, ownerId, friendUserId(缺省), stage/deviceUuid(缺省), callerRuntimeTarget
    // 勘误后显式全位参:files 8 位(friendUserId/stage/deviceUuid 缺省=undefined 占位)
    expect(mockedUseBotSessionFiles.mock.calls.at(-1)).toEqual(
      ['bot', 'session-1', 'viewer', '2088', undefined, undefined, undefined, 'service'],
    );
    // upload 链 7 位(无 friendUserId 位参;stage/deviceUuid 缺省=undefined 占位)
    expect(mockedUseBotSessionFileUpload.mock.calls.at(-1)).toEqual(
      ['bot', 'session-1', 'viewer', '2088', undefined, undefined, 'service'],
    );
    // key 断言:device_uuid 槽(第 7 位)不得被 caller 值顶入(勘误回归点)
    expect(mockedUseBotSessionFiles.mock.calls.at(-1)![6]).not.toBe('service');
  });
});

it('好友 Bot 探测到 caller 时传 caller_runtime_target=caller_instance(2026-11-19 勘误:与 f_user_id 并存合法,仅 service 并存 404)', async () => {
  mockGetCallerContext.mockResolvedValue({
    editable: false,
    botCallType: 'caller',
    mcpCallTypes: {},
    cliCallTypes: {},
  });
  renderHook(() => useBotSessionFilesFeature({ ...bot, botType: 'service', isFriendBot: true }, session, 'viewer', jest.fn()));
  await waitFor(() => {
    // 好友 Bot:friendUserId=userId + stage=online;caller 位 caller_instance(IAM 链同规则)
    expect(mockedUseBotSessionFiles.mock.calls.at(-1)).toEqual(
      ['bot', 'session-1', 'viewer', '2088', 'viewer', 'online', undefined, 'caller_instance'],
    );
    expect(mockedUseBotSessionFileUpload.mock.calls.at(-1)).toEqual(
      ['bot', 'session-1', 'viewer', '2088', 'online', undefined, 'caller_instance'],
    );
  });
});

it('探测结果非 caller 时两 Hook 不携带 callerRuntimeTarget', async () => {
  renderHook(() => useBotSessionFilesFeature({ ...bot, botType: 'service' }, session, 'viewer', jest.fn()));
  await waitFor(() => {
    expect(mockGetCallerContext).toHaveBeenCalled();
  });
  expect(mockedUseBotSessionFiles).toHaveBeenLastCalledWith('bot', 'session-1', 'viewer', '2088', undefined, undefined, undefined, undefined);
  expect(mockedUseBotSessionFileUpload).toHaveBeenLastCalledWith('bot', 'session-1', 'viewer', '2088', undefined, undefined, undefined);
});

it('探测失败静默降级:不携带、不重试、无提示', async () => {
  mockGetCallerContext.mockRejectedValue(new Error('boom'));
  const { result } = renderHook(() => useBotSessionFilesFeature({ ...bot, botType: 'service' }, session, 'viewer', jest.fn()));
  await waitFor(() => {
    expect(mockGetCallerContext).toHaveBeenCalledTimes(1);
  });
  expect(result.current.filesSupported).toBe(true);
  expect(mockedUseBotSessionFiles).toHaveBeenLastCalledWith('bot', 'session-1', 'viewer', '2088', undefined, undefined, undefined, undefined);
});
