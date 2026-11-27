/** @jest-environment jsdom */
/**
 * The definition library's write path: kind and the per-agent model.
 *
 * Both are the kind of field that fails silently when forgotten — a definition
 * saved without its kind ran as the wrong agent type, and one saved without its
 * model fell back to the Bot default with nothing to say so. These tests pin
 * what the form actually sends.
 */
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { DefinitionLibrary } from '@/components/agentswarm/DefinitionLibrary';
import { createDefinition, listDefinitions, updateDefinition } from '@/services/agentswarm/service';

// Mutable so a test can arrive as if from a run detail link. The `mock` prefix
// is what lets jest's hoisted factory reach it.
const mockSearchParams = { current: new URLSearchParams() };
jest.mock('@umijs/max', () => ({
  history: { push: jest.fn() },
  useSearchParams: () => [mockSearchParams.current, jest.fn()],
}));

jest.mock('@/services/agentswarm/service', () => ({
  listDefinitions: jest.fn(),
  createDefinition: jest.fn(),
  updateDefinition: jest.fn(),
  deleteDefinition: jest.fn(),
}));
jest.mock('@/components/ui/notify', () => ({ notifyError: jest.fn(), notifySuccess: jest.fn() }));
jest.mock('@/components/agentswarm/ManifestEditor', () => ({
  ManifestEditor: ({ value, onChange }: { value: string; onChange: (next: string) => void }) => (
    <textarea
      data-testid="manifest"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}));

const listMock = listDefinitions as jest.Mock;
const createMock = createDefinition as jest.Mock;
const updateMock = updateDefinition as jest.Mock;

function definition(overrides: Record<string, unknown> = {}) {
  return {
    definition_id: 'def-1',
    definition_row_id: 7,
    name: 'reviewer',
    role: 'checks things',
    instructions: '',
    kind: 'bot',
    model: 'antchat/Kimi-K2.6',
    manifest_document: '',
    manifest_digest: '',
    owner_id: 'u1',
    creator_id: 'u1',
    modifier_id: 'u1',
    created_at: 0,
    ...overrides,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockSearchParams.current = new URLSearchParams();
  listMock.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 });
  createMock.mockResolvedValue(definition());
  updateMock.mockResolvedValue(definition());
});

async function openCreate() {
  render(<DefinitionLibrary tenant="teamclaw" />);
  await waitFor(() => expect(screen.getByText('新建 Agent 定义')).toBeInTheDocument());
  fireEvent.click(screen.getByText('新建 Agent 定义'));
}

describe('DefinitionLibrary write path', () => {
  it('creates a definition as a bot kind', async () => {
    await openCreate();
    fireEvent.change(screen.getByLabelText(/名称/), { target: { value: 'new agent' } });
    fireEvent.click(screen.getByText('保存'));

    await waitFor(() => expect(createMock).toHaveBeenCalled());
    expect(createMock.mock.calls[0][1]).toMatchObject({ kind: 'bot' });
  });

  it('sends an empty model as null so it inherits the Bot default', async () => {
    await openCreate();
    fireEvent.change(screen.getByLabelText(/名称/), { target: { value: 'inheriting agent' } });
    fireEvent.click(screen.getByText('保存'));

    await waitFor(() => expect(createMock).toHaveBeenCalled());
    expect(createMock.mock.calls[0][1].model).toBeNull();
  });

  it('shows a qualified stored model as the known model it is', async () => {
    listMock.mockResolvedValue({ items: [definition()], total: 1, page: 1, page_size: 50 });

    render(<DefinitionLibrary tenant="teamclaw" />);
    fireEvent.click(await screen.findByLabelText(/编辑定义 reviewer/));

    // 'antchat/Kimi-K2.6' is the qualified spelling of the known 'Kimi-K2.6';
    // treating it as an unknown custom name made the dropdown disagree with
    // what the definition already stored.
    expect(screen.getByRole('combobox')).toHaveValue?.('');
    expect(screen.queryByPlaceholderText(/输入模型名称/)).not.toBeInTheDocument();
  });

  it('shows name, role and an instruction preview on each card', async () => {
    listMock.mockResolvedValue({
      items: [
        definition({
          name: 'reviewer',
          role: '检查竞品更新',
          instructions: '只输出结构化要点，不要寒暄，不要臆造来源。',
        }),
      ],
      total: 1,
      page: 1,
      page_size: 50,
    });

    render(<DefinitionLibrary tenant="teamclaw" />);

    // The card is the only place a saved agent is described; all three of these
    // have to be readable without opening the edit form, and each carries its
    // own label so the reader knows which field they are looking at.
    expect(await screen.findByTitle('reviewer')).toBeInTheDocument();
    // The name is the card's header, so it carries no field label of its own.
    expect(screen.queryByText('名称')).not.toBeInTheDocument();
    expect(screen.getByTitle('检查竞品更新')).toBeInTheDocument();
    expect(screen.getByText('职责')).toBeInTheDocument();
    expect(screen.getByText('只输出结构化要点，不要寒暄，不要臆造来源。')).toBeInTheDocument();
    expect(screen.getByText('指令')).toBeInTheDocument();
  });

  it('says so when a definition has no instructions of its own', async () => {
    listMock.mockResolvedValue({
      items: [definition({ role: '', instructions: '' })],
      total: 1,
      page: 1,
      page_size: 50,
    });

    render(<DefinitionLibrary tenant="teamclaw" />);

    expect(await screen.findByText(/未填写指令/)).toBeInTheDocument();
    // Both labelled fields say they are empty rather than showing a blank gap.
    expect(screen.getAllByText('未填写')).toHaveLength(1);
  });

  it('asks the gateway for the first page, nine at a time', async () => {
    render(<DefinitionLibrary tenant="teamclaw" />);

    await waitFor(() => expect(listMock).toHaveBeenCalled());
    // Nine fills the three-column grid exactly, and the page only ever shows a
    // screenful: the library grows with use.
    expect(listMock.mock.calls[0][1]).toMatchObject({ page: 1, page_size: 9 });
  });

  it('re-queries with the keyword instead of filtering what it already has', async () => {
    listMock.mockResolvedValue({ items: [definition()], total: 1, page: 1, page_size: 10 });
    render(<DefinitionLibrary tenant="teamclaw" />);
    await waitFor(() => expect(listMock).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText('搜索定义'), { target: { value: 'review' } });

    await waitFor(() =>
      expect(listMock.mock.calls.some((call) => call[1]?.name === 'review')).toBe(true),
    );
    // The page resets too: page 4 of the old result set would be empty.
    expect(listMock.mock.calls.at(-1)?.[1]).toMatchObject({ page: 1, name: 'review' });
  });

  it('does not offer paging when there is nothing to page', async () => {
    render(<DefinitionLibrary tenant="teamclaw" />);

    await waitFor(() => expect(listMock).toHaveBeenCalled());
    expect(screen.queryByText(/下一页|下一页/)).not.toBeInTheDocument();
  });

  it('names a taken name instead of blaming a concurrent edit', async () => {
    createMock.mockRejectedValue(
      new Error('definition_name_conflict: a definition named \'reviewer\' already exists'),
    );
    await openCreate();
    fireEvent.change(screen.getByLabelText(/名称/), { target: { value: 'reviewer' } });
    fireEvent.click(screen.getByText('保存'));

    // Both 409s used to read as "someone changed this under you", which sends
    // the user to refresh when the problem is the name they chose.
    expect(await screen.findByRole('alert')).toHaveTextContent('名称「reviewer」已被占用');
  });

  it('recognises a taken name from a backend that sends prose only', async () => {
    // Older deployments send no `definition_name_conflict` code, just this.
    createMock.mockRejectedValue(
      new Error("a live definition named 'agent_07' already exists"),
    );
    await openCreate();
    fireEvent.change(screen.getByLabelText(/名称/), { target: { value: 'agent_07' } });
    fireEvent.click(screen.getByText('保存'));

    expect(await screen.findByRole('alert')).toHaveTextContent('名称「agent_07」已被占用');
  });

  it('keeps the refresh advice for a version that moved on', async () => {
    createMock.mockRejectedValue(
      new Error('definition_version_conflict: definition 7 was superseded by row 9'),
    );
    await openCreate();
    fireEvent.change(screen.getByLabelText(/名称/), { target: { value: 'ok' } });
    fireEvent.click(screen.getByText('保存'));

    expect(await screen.findByRole('alert')).toHaveTextContent('该定义已被修改，请刷新后再试');
  });

  it('arriving from a run filters and highlights, but does not open the editor', async () => {
    // The reader came to see what a submission was made of, not to change it:
    // opening the editor put them one Save away from an accidental revision.
    mockSearchParams.current = new URLSearchParams('definition=def-1&name=reviewer');
    listMock.mockResolvedValue({ items: [definition()], total: 1, page: 1, page_size: 9 });

    render(<DefinitionLibrary tenant="teamclaw" />);

    // Narrowed to the one they came for...
    await waitFor(() =>
      expect(listMock.mock.calls.some((call) => call[1]?.name === 'reviewer')).toBe(true),
    );
    expect(await screen.findByTitle('reviewer')).toBeInTheDocument();
    // ...and no dialog.
    expect(screen.queryByText('编辑 Agent 定义')).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('keeps the stored model when revising a definition', async () => {
    listMock.mockResolvedValue({ items: [definition()], total: 1, page: 1, page_size: 50 });

    render(<DefinitionLibrary tenant="teamclaw" />);
    const edit = await screen.findByLabelText(/编辑定义 reviewer/);
    fireEvent.click(edit);
    fireEvent.change(screen.getByLabelText(/名称/), { target: { value: 'reviewer 2' } });
    fireEvent.click(screen.getByText('保存'));

    await waitFor(() => expect(updateMock).toHaveBeenCalled());
    // A revise that dropped the model would silently reset the agent to the
    // Bot default; the form has to echo what the definition already carried.
    expect(updateMock.mock.calls[0][1]).toMatchObject({
      kind: 'bot',
      model: 'antchat/Kimi-K2.6',
      name: 'reviewer 2',
    });
  });
});