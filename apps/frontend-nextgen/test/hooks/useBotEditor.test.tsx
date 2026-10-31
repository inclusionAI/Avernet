/** @jest-environment jsdom */
import { useBotEditor } from '@/hooks/useBotEditor';
import { botEditorService } from '@/services/botWorkshop/botEditorService';
import { act, renderHook, waitFor } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('@/services/botWorkshop/botEditorService', () => ({
  botEditorService: { load: jest.fn(), setSkillSetActive: jest.fn() },
}));
jest.mock('sonner', () => ({
  toast: { error: jest.fn(), success: jest.fn(), warning: jest.fn() },
}));

const mockedLoad = botEditorService.load as jest.MockedFunction<typeof botEditorService.load>;

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
  await waitFor(() => expect(mockedLoad).toHaveBeenCalledWith('bot-1', 'owner-1', 'cloud', ''));
});

it('桌面引擎能力上下文传入 Service，避免请求 Hermes 不支持的功能', async () => {
  mockedLoad.mockRejectedValue(new Error('test load stopped'));
  renderHook(() => useBotEditor('bot-1', false, 'space-1', true, 'owner-1', true, 'local', 'hermes'));
  await waitFor(() => expect(mockedLoad).toHaveBeenCalledWith('bot-1', 'owner-1', 'local', 'hermes'));
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

it('Skill 添加、能力集写入和上传透传当前页 Owner，切换 Owner 后使用新上下文', async () => {
  mockedLoad.mockRejectedValue(new Error('stop unrelated load'));
  const methods = [
    'setSkillSetSkill',
    'addSkillCenterReferences',
    'createSkillSet',
    'updateSkillSet',
    'deleteSkillSet',
    'uploadSkill',
    'uploadSkillFolder',
    'toggleSkill',
    'deleteSkill',
  ] as const;
  for (const method of methods)
    Object.assign(botEditorService, { [method]: jest.fn().mockResolvedValue({ id: 'local-1' }) });
  const { result, rerender } = renderHook(({ owner }) => useBotEditor('bot-1', true, 'space-1', true, owner), {
    initialProps: { owner: 'owner-1' },
  });
  await waitFor(() => expect(result.current.loading).toBe(false));
  await act(async () => {
    await result.current.setSkillSetSkill('set-1', 'skill-1', true);
    await result.current.addSkillCenterReferences('set-1', ['code-1']);
    await result.current.createSkillSet('test');
    await result.current.updateSkillSet('set-1', 'renamed');
    await result.current.deleteSkillSet('set-1');
    await result.current.uploadSkillFolder([]);
  });
  expect(botEditorService.setSkillSetSkill).toHaveBeenCalledWith('bot-1', 'set-1', 'skill-1', true, 'owner-1');
  expect(botEditorService.addSkillCenterReferences).toHaveBeenCalledWith('bot-1', 'set-1', ['code-1'], 'owner-1');
  expect(botEditorService.createSkillSet).toHaveBeenCalledWith('bot-1', 'test', 'owner-1');
  expect(botEditorService.updateSkillSet).toHaveBeenCalledWith('bot-1', 'set-1', 'renamed', 'owner-1');
  expect(botEditorService.deleteSkillSet).toHaveBeenCalledWith('bot-1', 'set-1', 'owner-1');
  expect(botEditorService.uploadSkillFolder).toHaveBeenCalledWith('bot-1', [], 'owner-1');
  rerender({ owner: 'owner-2' });
  await act(async () => {
    await result.current.setSkillSetSkill('set-1', 'skill-1', false);
  });
  expect(botEditorService.setSkillSetSkill).toHaveBeenLastCalledWith('bot-1', 'set-1', 'skill-1', false, 'owner-2');
});
