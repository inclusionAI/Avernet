/** @jest-environment jsdom */
import { useBotSessionFilesFeature } from '@/pages/Workspace/hooks/useBotSessionFilesFeature';
import { useBotSessionFileUpload } from '@/pages/Workspace/hooks/useBotSessionFileUpload';
import { useBotSkills } from '@/pages/Workspace/hooks/useBotSkills';
import { botSessionFileService } from '@/services/workspace/botSessionFileService';
import { useBotSessionFileStore } from '@/stores/botSessionFileStore';
import { act, renderHook, waitFor } from '@testing-library/react';

jest.mock('@/services/workspace/botSessionFileService');
jest.mock('@/pages/Workspace/hooks/useBotSessionFileUpload');
jest.mock('@/pages/Workspace/hooks/useBotSkills');
jest.mock('sonner');

it('未打开文件面板时,/file 自动展示服务端已有文件并可引用到输入框', async () => {
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
  const fileItems = () =>
    result.current.command.categories[0].items?.find((item) => item.name === 'file')?.subConfig?.categories[0].items ??
    [];
  await waitFor(() => expect(fileItems().map((item) => item.name)).toEqual(['已上传文件.txt']));
  expect(result.current.fileDrawerOpen).toBe(false);
  expect(botSessionFileService.loadReady).toHaveBeenCalledTimes(1);
  expect(botSessionFileService.loadReady).toHaveBeenCalledWith('bot', 'session', 'human', 'owner');
  const insertFileChips = jest.fn();
  Object.assign(result.current.senderRef, { current: { insertFileChips, focus: jest.fn() } });
  act(() => fileItems()[0].onSelect?.(fileItems()[0]));
  expect(insertFileChips).toHaveBeenCalledWith([{ resource_id: 'uploaded-1', display_name: '已上传文件.txt' }]);
});
