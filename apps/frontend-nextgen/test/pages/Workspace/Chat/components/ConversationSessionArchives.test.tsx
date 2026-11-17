/** @jest-environment jsdom */
import type {
  CodingBotSessionArchiveItem,
  CodingBotSessionArchivePreviewResult,
  ConversationSessionArchiveListState,
} from '@/domain/conversation/types';
import { ConversationSessionArchives } from '@/pages/Workspace/Chat/components/ConversationSessionArchives';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

const createArchiveItem = (
  sessionKey: string,
  sessionSource: 'mine' | 'others' | 'collab',
  title = `归档 ${sessionKey}`,
): CodingBotSessionArchiveItem => ({
  sessionKey,
  archiveId: `archive-${sessionKey}`,
  title,
  sessionKind: 'user',
  createdAt: null,
  updatedAt: null,
  lastActiveAt: null,
  archivedAt: null,
  messageCount: 12,
  restoredAt: null,
  restoreStatus: 'archived',
  restore: null,
  sessionSource,
});

const createState = (
  items: CodingBotSessionArchiveItem[],
  overrides: Partial<ConversationSessionArchiveListState> = {},
): ConversationSessionArchiveListState => ({
  items,
  nextCursor: null,
  catalogRevision: null,
  loading: false,
  error: null,
  isLoadingMore: false,
  loadMoreError: null,
  availability: 'supported',
  restoreAvailability: 'supported',
  operationIds: [],
  isSubmitting: false,
  restoreError: null,
  restoredItems: [],
  ...overrides,
});

const defaultPreviewResult: CodingBotSessionArchivePreviewResult = {
  ok: true,
  sessionKey: 'mine-1',
  archiveId: 'archive-mine-1',
  available: true,
  incomplete: false,
  first: [
    { role: 'user', text: '第一条用户消息', timestamp: null },
    { role: 'assistant', text: '第一条助手消息', timestamp: null },
  ],
  last: [
    { role: 'user', text: '最后一条用户消息', timestamp: null },
    { role: 'assistant', text: '最后一条助手消息', timestamp: null },
  ],
};

function renderArchives(
  props: {
    state: ConversationSessionArchiveListState;
    origin?: 'mine' | 'others' | 'collab';
    readOnly?: boolean;
    searchKeyword?: string;
    previewResult?: CodingBotSessionArchivePreviewResult;
  },
) {
  const onRestore = jest.fn(async () => true);
  const onPreview = jest.fn(async () => props.previewResult ?? defaultPreviewResult);
  render(
    <ConversationSessionArchives
      origin={props.origin ?? 'mine'}
      searchKeyword={props.searchKeyword ?? ''}
      state={props.state}
      readOnly={props.readOnly ?? false}
      onLoadMore={jest.fn()}
      onRetry={jest.fn()}
      onRestore={onRestore}
      onPreview={onPreview}
    />,
  );
  return { onRestore, onPreview };
}

beforeEach(() => {
  window.matchMedia ??= jest.fn().mockReturnValue({
    matches: false,
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
    addListener: jest.fn(),
    removeListener: jest.fn(),
  });
});

describe('ConversationSessionArchives', () => {
  it('reuses the bot-level session ID search and does not render a second archive search input', () => {
    renderArchives({
      state: createState([
        createArchiveItem('mine-1', 'mine', '我的归档会话'),
        createArchiveItem('mine-2', 'mine', '第二个归档会话'),
        createArchiveItem('other-1', 'others', '他人归档会话'),
      ]),
      searchKeyword: 'mine-1',
    });

    expect(screen.getByText('我的归档会话')).toBeInTheDocument();
    expect(screen.queryByText('第二个归档会话')).not.toBeInTheDocument();
    expect(screen.queryByText('他人归档会话')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('搜索归档会话')).not.toBeInTheDocument();
  });

  it('renders list empty states in the small sidebar scale', () => {
    renderArchives({
      state: createState([], {
        availability: 'unsupported',
        restoreAvailability: 'unsupported',
      }),
    });

    const title = screen.getByText('当前容器暂不支持归档');
    const iconBox = title.previousElementSibling;
    expect(title).toHaveClass('text-[13px]');
    expect(screen.getByText('请升级或重启容器后再查看已归档会话。')).toHaveClass('text-[11px]');
    expect(iconBox).toHaveClass('h-8', 'w-8');
    expect(iconBox?.firstElementChild).toHaveClass('h-4', 'w-4');
  });

  it('uses the compact TeamClaw row language without message counts or openclaw status cards', () => {
    renderArchives({
      state: createState([createArchiveItem('mine-1', 'mine')]),
    });

    expect(screen.queryByText('归档 · 可恢复')).not.toBeInTheDocument();
    expect(screen.queryByText('已加载 1 个归档会话')).not.toBeInTheDocument();
    expect(screen.queryByText('12 条')).not.toBeInTheDocument();
    expect(screen.queryByText('可恢复')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '批量恢复' })).toBeInTheDocument();
    expect(screen.getByText('批量恢复')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '归档会话操作' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看消息摘要' })).not.toBeInTheDocument();
  });

  it('keeps preview available but disables all restore actions in readonly mode', async () => {
    renderArchives({
      state: createState([createArchiveItem('mine-1', 'mine')]),
      readOnly: true,
    });

    expect(screen.queryByRole('button', { name: '批量恢复' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '恢复会话' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '归档会话操作' }));
    expect(screen.queryByRole('button', { name: '恢复会话' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看消息摘要' }));
    await waitFor(() => expect(screen.getByText('第一条用户消息')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: '恢复会话' })).not.toBeInTheDocument();
  });

  it('loads and shows an OpenClaw-aligned preview dialog', async () => {
    renderArchives({
      state: createState([createArchiveItem('mine-1', 'mine')]),
    });

    fireEvent.click(screen.getByRole('button', { name: '归档会话操作' }));
    fireEvent.click(screen.getByRole('button', { name: '查看消息摘要' }));
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('归档 mine-1')).toBeInTheDocument();
    expect(within(dialog).getByText('快速查看开头和最近消息；预览不会修改会话。')).toBeInTheDocument();
    expect(within(dialog).getByText('消息 12 条')).toBeInTheDocument();
    expect(within(dialog).getByText('开头消息')).toBeInTheDocument();
    expect(within(dialog).getByText('最近消息')).toBeInTheDocument();
    expect(within(dialog).getByText('第一条用户消息')).toBeInTheDocument();
    expect(within(dialog).getByText('最后一条助手消息')).toBeInTheDocument();
  });

  it('collapses duplicated short previews into one message section', async () => {
    renderArchives({
      state: createState([createArchiveItem('mine-1', 'mine')]),
      previewResult: {
        ...defaultPreviewResult,
        first: [{ role: 'user', text: '只有一条消息', timestamp: null }],
        last: [{ role: 'user', text: '只有一条消息', timestamp: null }],
      },
    });

    fireEvent.click(screen.getByRole('button', { name: '归档会话操作' }));
    fireEvent.click(screen.getByRole('button', { name: '查看消息摘要' }));
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('消息预览')).toBeInTheDocument();
    expect(within(dialog).queryByText('开头消息')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('最近消息')).not.toBeInTheDocument();
    expect(within(dialog).getAllByText('只有一条消息')).toHaveLength(1);
  });

  it('submits a single restore with focus enabled', async () => {
    const { onRestore } = renderArchives({
      state: createState([createArchiveItem('mine-1', 'mine')]),
    });

    fireEvent.click(screen.getByRole('button', { name: '归档会话操作' }));
    fireEvent.click(screen.getByRole('button', { name: '恢复会话' }));

    await waitFor(() =>
      expect(onRestore).toHaveBeenCalledWith({
        sessionKeys: ['mine-1'],
        focusRestored: true,
      }),
    );
  });

  it('submits selected sessions as a batch without focusing every session', async () => {
    const { onRestore } = renderArchives({
      state: createState([createArchiveItem('mine-1', 'mine'), createArchiveItem('mine-2', 'mine')]),
    });

    fireEvent.click(screen.getByRole('button', { name: '批量恢复' }));
    expect(screen.getByLabelText('选择 归档 mine-1').parentElement).toHaveStyle({ left: '40px' });
    expect(screen.getByLabelText('选择 归档 mine-1')).toHaveClass('size-3.5');
    fireEvent.click(screen.getByLabelText('选择 归档 mine-1'));
    fireEvent.click(screen.getByLabelText('选择 归档 mine-2'));
    expect(screen.getByText('已选 2 / 2')).toBeInTheDocument();
    expect(screen.getByText('归档 mine-1')).toHaveClass('text-foreground');
    fireEvent.click(screen.getByRole('button', { name: '恢复选中' }));

    await waitFor(() => expect(onRestore).toHaveBeenCalledTimes(1));
    expect(onRestore).toHaveBeenCalledWith({
      sessionKeys: ['mine-1', 'mine-2'],
      focusRestored: false,
    });
  });
});
