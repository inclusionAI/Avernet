/** @jest-environment jsdom */
import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import type { ConversationComposerProps } from '@/capabilities/conversationComposer';
import { DebugChatComposer } from '@/components/BotWorkshop/Editor/DebugChatComposer';
import { useBotSessionFiles } from '@/pages/Workspace/hooks/useBotSessionFiles';
import { useBotSessionFileUpload } from '@/pages/Workspace/hooks/useBotSessionFileUpload';
import { useBotSkills } from '@/pages/Workspace/hooks/useBotSkills';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { useBotSessionFileStore } from '@/stores/botSessionFileStore';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/pages/Workspace/hooks/useBotSessionFileUpload');
jest.mock('@/pages/Workspace/hooks/useBotSessionFiles');
jest.mock('@/pages/Workspace/hooks/useBotSkills');

const bot: ChatBotView = {
  botId: 'bot:owner',
  realBotId: 'bot',
  ownerId: 'owner',
  engineOwnerId: 'source-owner',
  displayName: 'Bot',
  chatable: true,
  online: true,
  botType: 'service',
  runtimeStage: 'verify',
};
const session: BotChatSessionView = {
  sessionId: 'session',
  botId: bot.botId,
  title: 'Debug',
  messageCount: 0,
  gmtCreate: '',
  gmtModified: '',
};
const file = {
  resourceId: 'resource-1',
  displayName: '需求说明.md',
  status: 'ready' as const,
  sizeBytes: 10,
  errorCode: null,
};
const onSend = jest.fn();
const onClear = jest.fn().mockResolvedValue(undefined);
const onStop = jest.fn();
const refresh = jest.fn(async () => {
  useBotSessionFileStore.getState().setReadyFiles([file]);
});
const loadSkills = jest.fn(async () => [
  { skillId: 'skill-1', name: 'test-skill', description: '测试技能', active: true, category: '' },
]);
const cancelAll = jest.fn();
function mount(overrides = {}) {
  return render(
    <DebugChatComposer
      bot={bot}
      session={session}
      userId="human"
      isRequesting={false}
      onSend={onSend}
      onStop={onStop}
      onClear={onClear}
      {...overrides}
    />,
  );
}
function input(container: HTMLElement) {
  return container.querySelector<HTMLElement>('[contenteditable]')!;
}
function type(element: HTMLElement, text: string) {
  element.textContent = text;
  element.focus();
  const range = document.createRange();
  range.selectNodeContents(element);
  range.collapse(false);
  window.getSelection()?.removeAllRanges();
  window.getSelection()?.addRange(range);
  fireEvent.input(element);
}

beforeAll(() => {
  Element.prototype.scrollIntoView = jest.fn();
});
beforeEach(() => {
  jest.clearAllMocks();
  useBotSessionFileStore.getState().resetForSession();
  jest.mocked(useBotSessionFiles).mockReturnValue({
    readyFiles: [],
    isLoadingList: false,
    refresh,
    deleteFile: jest.fn(),
    downloadFile: jest.fn(),
    previewFile: jest.fn(),
    addReadyFile: jest.fn(),
  });
  jest.mocked(useBotSkills).mockReturnValue({ skills: [], isLoading: false, loadSkills });
  jest.mocked(useBotSessionFileUpload).mockReturnValue({
    tasks: [],
    isUploading: false,
    stageFiles: jest.fn(),
    removeTask: jest.fn(),
    cancelAll,
    submit: jest.fn(async (ready) => {
      ready?.(file);
    }),
  });
});
afterEach(() => extendCapabilities({ getConversationComposer: defaultCapabilities.getConversationComposer }));

it('使用真实 Sender 唤起公共命令，只有进入子菜单才加载资源', async () => {
  const { container } = mount();
  expect(refresh).not.toHaveBeenCalled();
  expect(loadSkills).not.toHaveBeenCalled();
  type(input(container), '/');
  expect(await screen.findByText('file', { exact: true })).toBeVisible();
  expect(screen.getByText('skill', { exact: true })).toBeVisible();
  expect(screen.getByText('clear', { exact: true })).toBeVisible();
  expect(refresh).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('file', { exact: true }));
  expect(await screen.findByText(file.displayName, { exact: true })).toBeVisible();
  expect(refresh).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByText(file.displayName, { exact: true }));
  await waitFor(() => expect(input(container).textContent).toContain(file.displayName));
  fireEvent.keyDown(input(container), { key: 'Enter' });
  await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
  expect(onSend.mock.calls[0][0]).toContain('<file-ref insert_id=');
  expect(onSend.mock.calls[0][1].resourceReferences).toEqual([
    expect.objectContaining({ type: 'file', resource_id: 'resource-1' }),
  ]);
  expect(jest.mocked(useBotSessionFiles)).toHaveBeenCalledWith('bot', 'session', 'human', 'owner', undefined, 'verify');
});

it('上传完成后插入真实文件 chip，可以只发送附件', async () => {
  const { container } = mount();
  fireEvent.change(screen.getByLabelText('选择调试会话文件'), {
    target: { files: [new File(['hello'], file.displayName, { type: 'text/markdown' })] },
  });
  await waitFor(() => expect(input(container).textContent).toContain(file.displayName));
  fireEvent.keyDown(input(container), { key: 'Enter' });
  await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
  expect(onSend.mock.calls[0][1].promptFileRefs[0].resource_id).toBe('resource-1');
  expect(useBotSessionFileUpload).toHaveBeenCalledWith('bot', 'session', 'human', 'owner', 'verify');
});

it('选择技能只插入一次命令，不将选择用的 Enter 当成消息发送', async () => {
  const { container } = mount();
  type(input(container), '/skill');
  await screen.findByText('skill', { exact: true });
  fireEvent.keyDown(input(container), { key: 'Enter' });
  await screen.findByText('test-skill', { exact: true });
  fireEvent.keyDown(input(container), { key: 'Enter' });
  expect(input(container).textContent).toContain('/test-skill');
  expect(input(container).textContent).not.toContain('/skill/');
  expect(onSend).not.toHaveBeenCalled();
});

it('clear 复用宿主清空动作，不发送 slash 文本', async () => {
  const { container } = mount();
  type(input(container), '/clear');
  fireEvent.click(await screen.findByText('clear', { exact: true }));
  await waitFor(() => expect(onClear).toHaveBeenCalledTimes(1));
  expect(onSend).not.toHaveBeenCalled();
});

it('平台扩展同时装饰菜单、提示、header 和文件发送上下文', async () => {
  let observed: ConversationComposerProps['context'] | undefined;
  function Extension({ context, children, command, onSend, onInvokeCommand }: ConversationComposerProps) {
    observed = context;
    return children({
      command,
      placeholder: '扩展输入提示',
      header: <span>引用知识库 kb-test</span>,
      extraMenuRows: (close) => (
        <button
          type="button"
          onClick={() => {
            close();
            onInvokeCommand?.('/knowledge');
          }}
        >
          知识库
        </button>
      ),
      onSend: (content, submit) =>
        onSend(
          `kb-test\n${content}`,
          submit && {
            ...submit,
            resolvedContent: submit.resolvedContent ? `kb-test\n${submit.resolvedContent}` : undefined,
          },
        ),
    });
  }
  extendCapabilities({ getConversationComposer: () => ({ status: 'available', value: Extension }) });
  const { container } = mount();
  expect(observed).toMatchObject({
    botId: 'bot',
    ownerId: 'owner',
    knowledgeOwnerId: 'source-owner',
    runtimeStage: 'verify',
    sessionId: 'session',
  });
  expect(screen.getByText('引用知识库 kb-test')).toBeVisible();
  expect(input(container)).toHaveAttribute('data-placeholder', '扩展输入提示');
  fireEvent.change(screen.getByLabelText('选择调试会话文件'), {
    target: { files: [new File(['x'], file.displayName)] },
  });
  await waitFor(() => expect(input(container).textContent).toContain(file.displayName));
  fireEvent.keyDown(input(container), { key: 'Enter' });
  await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
  expect(onSend.mock.calls[0][0]).toMatch(/^kb-test\n<file-ref /);
  expect(onSend.mock.calls[0][1].resourceReferences[0].resource_id).toBe('resource-1');
});

it('没有会话时禁用输入，生成中保留停止动作', () => {
  const { container, rerender } = mount({ session: null });
  expect(input(container)).toHaveAttribute('contenteditable', 'false');
  rerender(
    <DebugChatComposer
      bot={bot}
      session={session}
      userId="human"
      isRequesting
      onSend={onSend}
      onStop={onStop}
      onClear={onClear}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: '取消生成' }));
  expect(onStop).toHaveBeenCalledTimes(1);
});

it('卸载时取消上传，旧会话上传回调不向新输入框插入附件', async () => {
  let ready: ((uploaded: typeof file) => void) | undefined;
  jest.mocked(useBotSessionFileUpload).mockReturnValue({
    tasks: [],
    isUploading: false,
    stageFiles: jest.fn(),
    removeTask: jest.fn(),
    cancelAll,
    submit: jest.fn(async (callback) => {
      ready = callback;
    }),
  });
  const previous = mount();
  fireEvent.change(screen.getByLabelText('选择调试会话文件'), {
    target: { files: [new File(['x'], file.displayName)] },
  });
  await waitFor(() => expect(ready).toBeDefined());
  previous.unmount();
  expect(cancelAll).toHaveBeenCalled();
  const current = mount({ session: { ...session, sessionId: 'other' } });
  ready?.(file);
  expect(input(current.container).textContent).not.toContain(file.displayName);
});

it('模型切换保留在发送按钮左侧', () => {
  mount({ modelSelector: <span data-testid="model-selector">模型选择</span> });
  expect(
    screen.getByTestId('model-selector').compareDocumentPosition(screen.getByRole('button', { name: '发送消息' })) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});
