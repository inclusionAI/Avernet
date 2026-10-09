/** @jest-environment jsdom */
import { DebugChatComposer } from '@/components/BotWorkshop/Editor/DebugChatComposer';
import { useBotSessionFileUpload } from '@/pages/Workspace/hooks/useBotSessionFileUpload';
import { expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/pages/Workspace/hooks/useBotSessionFileUpload');

const mockedUpload = useBotSessionFileUpload as jest.Mock;

it('上传完成后以文件引用发送调试消息', async () => {
  const stageFiles = jest.fn();
  const cancelAll = jest.fn();
  mockedUpload.mockReturnValue({
    tasks: [],
    isUploading: false,
    stageFiles,
    removeTask: jest.fn(),
    cancelAll,
    submit: jest.fn(async (onReady: (file: unknown) => void) => {
      onReady({
        resourceId: 'resource-1',
        displayName: '需求说明.md',
        status: 'ready',
        sizeBytes: 10,
        errorCode: null,
      });
    }),
  });
  const onSend = jest.fn();
  const file = new File(['content'], '需求说明.md', { type: 'text/markdown' });

  render(
    <DebugChatComposer
      botId="bot-1"
      sessionId="session-1"
      userId="user-1"
      ownerId="owner-1"
      isRequesting={false}
      onSend={onSend}
      onStop={jest.fn()}
    />,
  );

  fireEvent.change(screen.getByLabelText('选择调试会话文件'), { target: { files: [file] } });
  await waitFor(() => expect(screen.getByText('需求说明.md')).toBeInTheDocument());
  expect(stageFiles).toHaveBeenCalledWith([file]);

  fireEvent.click(screen.getByRole('button', { name: '发送' }));
  expect(onSend).toHaveBeenCalledWith('<file-ref insert_id="debug_file_1"></file-ref>', {
    resourceReferences: [{ type: 'file', resource_id: 'resource-1', insert_id: 'debug_file_1' }],
    promptFileRefs: [{ resource_id: 'resource-1', insert_id: 'debug_file_1' }],
    fileRefDisplay: [{ insert_id: 'debug_file_1', name: '需求说明.md' }],
  });
});
