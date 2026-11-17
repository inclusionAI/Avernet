/** @jest-environment jsdom */
import { useBotEditor } from '@/hooks/useBotEditor';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { act, renderHook, waitFor } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('@/services/botWorkshop/botEditorService', () => ({
  botEditorService: { load: jest.fn(), loadCapabilityCandidates: jest.fn(), setSkillSetActive: jest.fn(), listResources: jest.fn() },
}));
jest.mock('sonner', () => ({
  toast: { error: jest.fn(), success: jest.fn(), warning: jest.fn() },
}));

const mockedLoad = botEditorService.load as jest.MockedFunction<typeof botEditorService.load>;
const mockedLoadCapabilityCandidates = botEditorService.loadCapabilityCandidates as jest.MockedFunction<
  typeof botEditorService.loadCapabilityCandidates
>;

beforeEach(() => { jest.mocked(botEditorService.listResources).mockReset().mockResolvedValue([]); });
afterEach(() => jest.clearAllMocks());

it('编辑配置加载异常后结束 loading，避免页面永久转圈', async () => {
  mockedLoad.mockRejectedValueOnce(new Error('能力集子资源加载失败'));

  const { result } = renderHook(() => useBotEditor('bot-1'));

  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(toast.error).toHaveBeenCalledWith('能力集子资源加载失败');
});

it('等待 Bot 详情就绪后使用 Owner 加载本地 Skill', async () => {
  mockedLoad.mockRejectedValue(new Error('test load stopped'));
  const initialProps: { enabled: boolean; ownerId?: string } = { enabled: false };
  const { rerender } = renderHook(
    ({ enabled, ownerId }: { enabled: boolean; ownerId?: string }) =>
      useBotEditor('bot-1', false, 'space-1', enabled, ownerId),
    { initialProps },
  );
  expect(mockedLoad).not.toHaveBeenCalled();
  rerender({ enabled: true, ownerId: 'owner-1' });
  await waitFor(() => expect(mockedLoad).toHaveBeenCalledWith('bot-1', 'owner-1', 'cloud', '', true));
});

it('桌面引擎能力上下文传入 Service，避免请求 Hermes 不支持的功能', async () => {
  mockedLoad.mockRejectedValue(new Error('test load stopped'));
  renderHook(() => useBotEditor('bot-1', false, 'space-1', true, 'owner-1', true, 'local', 'hermes'));
  await waitFor(() => expect(mockedLoad).toHaveBeenCalledWith('bot-1', 'owner-1', 'local', 'hermes', true));
});

it('协作者打开 MCP 候选时使用 Bot Owner 寻址', async () => {
  mockedLoad.mockRejectedValue(new Error('test load stopped'));
  mockedLoadCapabilityCandidates.mockResolvedValue({
    availableMcps: [],
    marketSkills: [],
    skillCenterSkills: [],
    workshopSkills: [],
  });
  const { result } = renderHook(() => useBotEditor('bot-1', false, 'space-1', true, 'owner-1'));

  await act(async () => {
    await result.current.loadCapabilityCandidates('mcp', { source: 'internal' });
  });

  expect(mockedLoadCapabilityCandidates).toHaveBeenCalledWith(
    'bot-1',
    'space-1',
    'mcp',
    { source: 'internal' },
    'owner-1',
  );
});

it('能力集停用只更新对应状态，不触发整页配置重载', async () => {
  const set = { id: 'set-1', name: '个人能力集', isDefault: false, active: true, skills: [], mcps: [], clis: [] };
  mockedLoad.mockResolvedValueOnce({
    skills: [],
    skillSets: [set],
    mcps: [],
    availableMcps: [],
    marketSkills: [],
    workshopSkills: [],
    resources: [],
    screens: [],
    routines: [],
    engineStatus: undefined,
    errors: 0,
  });
  jest.mocked(botEditorService.setSkillSetActive).mockResolvedValueOnce({ id: 'set-1', active: false });
  const { result } = renderHook(() => useBotEditor('bot-1'));
  await waitFor(() => expect(result.current.loading).toBe(false));
  await act(async () => {
    await result.current.setSkillSetActive(set, false);
  });
  expect(result.current.skillSets[0].active).toBe(false);
  expect(result.current.loading).toBe(false);
  expect(mockedLoad).toHaveBeenCalledTimes(1);
});


function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}
const emptyLoad = () => ({ skills: [], skillSets: [], mcps: [], availableMcps: [], marketSkills: [], workshopSkills: [], resources: [], screens: [], routines: [], engineStatus: undefined, errors: 0 });
const item = (path: string, parentPath = '', type: 'file' | 'folder' = 'file') => ({ path, parentPath, name: path.split('/').pop()!, type });

it.each([502, 503])('离线首次加载（%s）显示失败，手动重试成功后显示真实结果', async (status) => {
  mockedLoad.mockResolvedValueOnce(emptyLoad());
  jest.mocked(botEditorService.listResources)
    .mockRejectedValueOnce({ status, data: { message: 'Bot device is offline' } })
    .mockResolvedValueOnce([item('file.txt')]);
  const { result } = renderHook(() => useBotEditor('bot-1'));
  await waitFor(() => expect(result.current.resourceErrors['']).toBe('Bot 暂时离线，请稍后重试'));
  await act(async () => { await result.current.browseResources(''); });
  expect(result.current.resources.map((x) => x.path)).toEqual(['file.txt']);
  expect(result.current.resourceErrors['']).toBeUndefined();
});

it('旧根加载晚于重试完成时不会覆盖新列表', async () => {
  const slow = deferred<ReturnType<typeof item>[]>();
  mockedLoad.mockResolvedValueOnce(emptyLoad());
  jest.mocked(botEditorService.listResources).mockReturnValueOnce(slow.promise).mockResolvedValueOnce([item('new.txt')]);
  const { result } = renderHook(() => useBotEditor('bot-1'));
  await act(async () => { await result.current.browseResources(''); });
  await act(async () => { slow.resolve([item('old.txt')]); await slow.promise; });
  expect(result.current.resources.map((x) => x.path)).toEqual(['new.txt']);
});

it('根刷新使在途子目录失效，旧子项不能复活', async () => {
  mockedLoad.mockResolvedValueOnce(emptyLoad());
  const child = deferred<ReturnType<typeof item>[]>();
  jest.mocked(botEditorService.listResources).mockResolvedValueOnce([item('docs', '', 'folder')]).mockReturnValueOnce(child.promise).mockResolvedValueOnce([item('docs', '', 'folder')]);
  const { result } = renderHook(() => useBotEditor('bot-1'));
  await waitFor(() => expect(result.current.resourceLoadedPaths).toContain(''));
  let pending!: Promise<void>;
  act(() => { pending = result.current.loadResourceDirectory('docs').catch(() => undefined); });
  await act(async () => { await result.current.browseResources(''); });
  await act(async () => { child.resolve([item('docs/old', 'docs')]); await pending; });
  expect(result.current.resources.map((x) => x.path)).toEqual(['docs']);
  expect(result.current.resourceLoadedPaths).not.toContain('docs');
});

it.each([502, 503])('通用 %s 不误称设备离线，重试为空时是已确认空列表', async (status) => {
  mockedLoad.mockResolvedValueOnce(emptyLoad());
  jest.mocked(botEditorService.listResources).mockRejectedValueOnce({ status, data: { message: 'other outage' } }).mockResolvedValueOnce([]);
  const { result } = renderHook(() => useBotEditor('bot-1'));
  await waitFor(() => expect(result.current.resourceErrors['']).toBe('资源加载失败，请重试'));
  await act(async () => { await result.current.browseResources(''); });
  expect(result.current.resources).toEqual([]);
  expect(result.current.resourceErrors['']).toBeUndefined();
  expect(result.current.resourceLoadedPaths).toContain('');
});

it('Bot A→B→A 后最早的 A 请求不能覆盖新 A', async () => {
  const old = deferred<ReturnType<typeof item>[]>();
  mockedLoad.mockResolvedValue(emptyLoad());
  jest.mocked(botEditorService.listResources).mockReturnValueOnce(old.promise).mockResolvedValueOnce([item('b')]).mockResolvedValueOnce([item('new')]);
  const { result, rerender } = renderHook(({ botId }) => useBotEditor(botId), { initialProps: { botId: 'bot-a' } });
  rerender({ botId: 'bot-b' });
  await waitFor(() => expect(result.current.resources.map((x) => x.path)).toEqual(['b']));
  rerender({ botId: 'bot-a' });
  await waitFor(() => expect(result.current.resources.map((x) => x.path)).toEqual(['new']));
  await act(async () => { old.resolve([item('old')]); await old.promise; });
  expect(result.current.resources.map((x) => x.path)).toEqual(['new']);
});

it('其他配置永久等待时资源离线仍立即显示', async () => {
  mockedLoad.mockReturnValueOnce(new Promise(() => {}));
  jest.mocked(botEditorService.listResources).mockRejectedValueOnce({ status: 503, data: { message: 'Bot device is offline' } });
  const { result } = renderHook(() => useBotEditor('bot-1'));
  await waitFor(() => expect(result.current.resourceErrors['']).toBe('Bot 暂时离线，请稍后重试'));
});
