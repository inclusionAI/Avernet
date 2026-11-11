/** @jest-environment jsdom */
import { useBotSessionCommands } from '@/pages/Workspace/hooks/useBotSessionCommands';
import type { UseBotSessionFilesResult } from '@/pages/Workspace/hooks/useBotSessionFiles';
import type { UseBotSkillsResult } from '@/pages/Workspace/hooks/useBotSkills';
import { useBotSessionFileStore } from '@/stores/botSessionFileStore';
import { act, renderHook, waitFor } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('sonner');
const reference = jest.fn();
const focus = jest.fn();
const clear = jest.fn();
const skills: UseBotSkillsResult = { skills: [], isLoading: false, loadSkills: jest.fn(async () => []) };
const files: UseBotSessionFilesResult = {
  readyFiles: [],
  isLoadingList: false,
  refresh: jest.fn(async () => {}),
  addReadyFile: jest.fn(),
  deleteFile: jest.fn(),
  downloadFile: jest.fn(),
  previewFile: jest.fn(),
};
function useCommands(overrides = {}) {
  return useBotSessionCommands({
    enabled: true,
    canUseSkills: true,
    files,
    skillsState: skills,
    handleReference: reference,
    handleSelectSkill: focus,
    onClear: clear,
    ...overrides,
  });
}
const entry = (command: ReturnType<typeof useCommands>, name: string) =>
  command.categories[0].items!.find((item) => item.name === name)!;

beforeEach(() => {
  jest.clearAllMocks();
  useBotSessionFileStore.getState().resetForSession();
});

it('每次进入 file 子菜单刷新完整候选，不在普通渲染时查询', async () => {
  const refresh = jest.fn(async () => {
    useBotSessionFileStore
      .getState()
      .setReadyFiles([{ resourceId: 'r1', displayName: '接口.md', status: 'ready', sizeBytes: 1, errorCode: null, creatorType: null, creatorName: null, createdAt: null }]);
  });
  const scopedFiles = { ...files, refresh };
  const { result } = renderHook(() => useCommands({ files: scopedFiles }));
  expect(refresh).not.toHaveBeenCalled();
  await act(async () => {
    result.current.onSelect?.(entry(result.current, 'file'));
  });
  const category = entry(result.current, 'file').subConfig!.categories[0];
  expect(category.onSearch).toBeUndefined(); // SDK 负责本地过滤，含 `/` 空查询进入子菜单。
  expect(category.items?.map((item) => item.name)).toEqual(['接口.md']);
  act(() => category.items![0].onSelect?.(category.items![0]));
  expect(reference).toHaveBeenCalledWith(expect.objectContaining({ resourceId: 'r1' }));
  await act(async () => {
    result.current.onSelect?.(entry(result.current, 'file'));
  });
  expect(refresh).toHaveBeenCalledTimes(2);
});

it('切换查询作用域后丢弃上一会话的候选响应', async () => {
  let finish!: () => void;
  const previous = {
    ...files,
    refresh: jest.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    ),
  };
  const next = { ...files, refresh: jest.fn(async () => {}) };
  const { result, rerender } = renderHook(({ scope }) => useCommands({ files: scope }), {
    initialProps: { scope: previous },
  });
  act(() => result.current.onSelect?.(entry(result.current, 'file')));
  rerender({ scope: next });
  useBotSessionFileStore
    .getState()
    .setReadyFiles([{ resourceId: 'old', displayName: '旧文件.md', status: 'ready', sizeBytes: 1, errorCode: null, creatorType: null, creatorName: null, createdAt: null }]);
  await act(async () => {
    finish();
  });
  expect(entry(result.current, 'file').subConfig!.categories[0].items).toEqual([]);
});

it('加载失败可观察并且重新进入能够重试', async () => {
  const refresh = jest.fn().mockRejectedValueOnce(new Error('文件列表失败')).mockResolvedValue(undefined);
  const scopedFiles = { ...files, refresh };
  const { result } = renderHook(() => useCommands({ files: scopedFiles }));
  await act(async () => {
    result.current.onSelect?.(entry(result.current, 'file'));
  });
  await waitFor(() => expect(toast.error).toHaveBeenCalledWith('文件列表失败'));
  await act(async () => {
    result.current.onSelect?.(entry(result.current, 'file'));
  });
  expect(refresh).toHaveBeenCalledTimes(2);
});

it('未就绪身份不查询资源，好友 Bot 不提供 skill 命令', async () => {
  const { result } = renderHook(() => useCommands({ enabled: false, canUseSkills: false }));
  expect(result.current.categories[0].items?.map((item) => item.name)).toEqual(['file', 'clear']);
  await act(async () => {
    result.current.onSelect?.(entry(result.current, 'file'));
  });
  expect(files.refresh).not.toHaveBeenCalled();
  expect(skills.loadSkills).not.toHaveBeenCalled();
});
