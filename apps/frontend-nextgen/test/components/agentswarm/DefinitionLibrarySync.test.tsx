/** @jest-environment jsdom */
/**
 * 指令 and the capability YAML's SOUL.md are one text, edited from either side.
 *
 * The definition library had them as two independent fields: typing 指令 left
 * the YAML's SOUL.md stale, so the definition created here could disagree with
 * what the persona actually said. The config page already kept them in step;
 * this pins the library to the same contract.
 */
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { DefinitionLibrary } from '@/components/agentswarm/DefinitionLibrary';
import { createDefinition, listDefinitions } from '@/services/agentswarm/service';

jest.mock('@umijs/max', () => ({
  history: { push: jest.fn() },
  useSearchParams: () => [new URLSearchParams(), jest.fn()],
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

beforeEach(() => {
  jest.clearAllMocks();
  listMock.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 });
  createMock.mockResolvedValue({});
});

async function openCreate() {
  render(<DefinitionLibrary tenant="teamclaw" />);
  fireEvent.click(await screen.findByText('新建 Agent 定义'));
  fireEvent.change(screen.getByLabelText(/名称/), { target: { value: 'synced' } });
}

describe('the definition library keeps 指令 and SOUL.md in step', () => {
  it('typing 指令 writes it into the YAML SOUL.md content', async () => {
    await openCreate();
    fireEvent.change(screen.getByLabelText('指令'), { target: { value: '只说重点' } });

    // The field the user did not touch must already agree with the one they did.
    expect((screen.getByTestId('manifest') as HTMLTextAreaElement).value).toContain('只说重点');
  });

  it('editing the YAML SOUL.md writes it back to 指令', async () => {
    await openCreate();
    const manifest = [
      'schema_version: 1',
      'manifest:',
      '  identity:',
      '    - type: SOUL.md',
      '      content: |-',
      '        来自 YAML',
    ].join('\n');
    fireEvent.change(screen.getByTestId('manifest'), { target: { value: manifest } });

    await waitFor(() =>
      expect((screen.getByLabelText('指令') as HTMLTextAreaElement).value).toBe('来自 YAML'),
    );
  });

  it('opens with an empty persona, not a canned one', async () => {
    await openCreate();
    const opened = (screen.getByTestId('manifest') as HTMLTextAreaElement).value;
    // The shape is there to fill in; the persona is not written for the user.
    expect(opened).toContain('type: SOUL.md');
    expect(opened).toContain('content: ""');
    expect(opened).not.toContain('general-purpose assistant');
  });

  it('stores a real document even when the editor was not touched', async () => {
    await openCreate();
    fireEvent.click(screen.getByText('保存'));

    await waitFor(() => expect(createMock).toHaveBeenCalled());
    // Every definition has to carry its YAML: without one, each reader would
    // synthesize the persona again just to show what it is.
    const sent = createMock.mock.calls[0][1] as { manifest_document: string };
    expect(sent.manifest_document).toContain('SOUL.md');
    expect(sent.manifest_document).toContain('schema_version');
  });

  it('sends one document, not two disagreeing views', async () => {
    await openCreate();
    fireEvent.change(screen.getByLabelText('指令'), { target: { value: '唯一内容' } });
    fireEvent.click(screen.getByText('保存'));

    await waitFor(() => expect(createMock).toHaveBeenCalled());
    const sent = createMock.mock.calls[0][1] as { instructions: string; manifest_document: string };
    expect(sent.instructions).toBe('唯一内容');
    expect(sent.manifest_document).toContain('唯一内容');
  });
});
