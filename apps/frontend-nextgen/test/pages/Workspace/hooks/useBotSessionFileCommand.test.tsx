/** @jest-environment jsdom */
import { useBotSessionFilesFeature } from '@/pages/Workspace/hooks/useBotSessionFilesFeature';
import { useBotSessionFileUpload } from '@/pages/Workspace/hooks/useBotSessionFileUpload';
import { useBotSkills } from '@/pages/Workspace/hooks/useBotSkills';
import { botSessionFileService } from '@/services/workspace/botSessionFileService';
import { useBotSessionFileStore } from '@/stores/botSessionFileStore';
import type { CommandItem } from '@tc-chat/ui/es/Sender';
import { act, renderHook } from '@testing-library/react';

jest.mock('@/services/workspace/botSessionFileService');
jest.mock('@/pages/Workspace/hooks/useBotSessionFileUpload');
jest.mock('@/pages/Workspace/hooks/useBotSkills');
jest.mock('sonner');

it('未打开文件面板时,/file 唤起经 onSearch 动态拉取服务端已有文件并可引用到输入框', async () => {
  useBotSessionFileStore.getState().resetForSession();
  jest.mocked(useBotSessionFileUpload).mockReturnValue({
    tasks: [],
    isUploading: false,
    submit: jest.fn(),
    removeTask: jest.fn(),
  } as unknown as ReturnType<typeof useBotSessionFileUpload>);
  jest.mocked(useBotSkills).mockReturnValue({
    skills: [],
    isLoading: false,
    loadSkills: jest.fn(),
  } as unknown as ReturnType<typeof useBotSkills>);
  jest.mocked(botSessionFileService.loadReady).mockResolvedValue({
    ok: true,
    data: {
      items: [
        { resourceId: 'uploaded-1', displayName: '已上传文件.txt', status: 'ready', sizeBytes: 10, errorCode: null },
      ],
      total: 1,
    },
  });
  const { result } = renderHook(() =>
    useBotSessionFilesFeature(
      { botId: 'bot:owner', realBotId: 'bot', ownerId: 'owner', displayName: 'Bot', chatable: true, online: true },
      { sessionId: 'session', botId: 'bot:owner', title: 'Session', messageCount: 0, gmtModified: '', gmtCreate: '' },
      'human',
      jest.fn(),
    ),
  );
  const fileOnSearch = () =>
    result.current.command.categories[0].items?.find((item) => item.name === 'file')?.subConfig?.categories[0]
      .onSearch;
  // 懒加载契约(2026-10-21):挂载不预加载,/file 唤起前不调接口,清单经 onSearch 动态拉取。
  expect(typeof fileOnSearch()).toBe('function');
  expect(botSessionFileService.loadReady).not.toHaveBeenCalled();
  // /file 唤起:经 onSearch 动态拉取(每次唤起都调接口),返回服务端最新文件清单。
  let items: CommandItem[] = [];
  await act(async () => {
    items = (await fileOnSearch()?.('')) ?? [];
  });
  expect(items.map((item) => item.name)).toEqual(['已上传文件.txt']);
  expect(result.current.fileDrawerOpen).toBe(false);
  expect(botSessionFileService.loadReady).toHaveBeenCalledTimes(1);
  expect(botSessionFileService.loadReady).toHaveBeenCalledWith('bot', 'session', 'human', 'owner', undefined);
  const insertFileChips = jest.fn();
  Object.assign(result.current.senderRef, { current: { insertFileChips, focus: jest.fn() } });
  act(() => items[0].onSelect?.(items[0]));
  expect(insertFileChips).toHaveBeenCalledWith([{ resource_id: 'uploaded-1', display_name: '已上传文件.txt' }]);
});
