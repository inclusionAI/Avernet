/** @jest-environment jsdom */
/**
 * Name lookups on 新建计算任务.
 *
 * The library is paged, so anything that relied on the definitions already
 * loaded only ever saw the first page. Two things depended on that: the
 * collision check (a taken name reached the gateway and came back 409) and
 * 填入示例任务 (an agent that is already stored stayed a blank entry instead of
 * becoming a reference). Both now ask the gateway by name.
 */
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import AgentComputeNewPage from '@/pages/AgentCompute/New';
import { getCapabilities } from '@/capabilities';

// `mock`-prefixed so jest's hoisted factory can reach it.
const mockListDefinitions = jest.fn();
jest.mock('@/services/agentswarm/service', () => ({
  listDefinitions: (...args: unknown[]) => mockListDefinitions(...args),
  createDefinition: jest.fn(),
  updateDefinition: jest.fn(),
  deleteDefinition: jest.fn(),
  submitRun: jest.fn(),
}));
jest.mock('@umijs/max', () => ({
  history: { push: jest.fn() },
  useSearchParams: () => [new URLSearchParams(), jest.fn()],
}));
jest.mock('@/pages/Workspace/hooks/useOwnedBots', () => ({
  useOwnedBots: () => ({
    chatBots: [
      { botId: 'b1', realBotId: 'b1', displayName: '执行 Bot', online: true, chatable: true, status: 'ACTIVE' },
    ],
    isLoading: false,
    error: null,
    hasAgentCodingBots: false,
    reload: jest.fn(),
  }),
}));
jest.mock('@/components/ui/notify', () => ({ notifyError: jest.fn(), notifySuccess: jest.fn() }));
jest.mock('@/pages/Workspace/components/Modals/YamlEditor', () => ({
  YamlCodeEditor: () => <textarea data-testid="yaml-code-editor" />,
}));
jest.mock('@/domain/agentswarm/examples', () => ({
  AGENT_EXAMPLES: [
    {
      goal: '产出 hello world',
      summary: '示例',
      agents: [{ name: 'hello_writer', role: '产出 hello', instructions: '只回复 hello' }],
    },
  ],
}));

const capabilities = getCapabilities();

beforeEach(() => {
  jest.restoreAllMocks();
  mockListDefinitions.mockReset();
  jest
    .spyOn(capabilities, 'getHumanIdentity')
    .mockReturnValue({ status: 'available', value: { userId: '49912', displayName: 'dev', online: true } });
  mockListDefinitions.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 9 });
});

async function waitForNameField(): Promise<HTMLInputElement> {
  await waitFor(() =>
    expect(document.querySelector('input[id^="compute-name-"]')).not.toBeNull(),
  );
  return document.querySelector<HTMLInputElement>('input[id^="compute-name-"]')!;
}

async function typeName(value: string) {
  const input = await waitForNameField();
  fireEvent.change(input, { target: { value } });
  fireEvent.blur(input);
}

describe('新建计算任务的名称查重', () => {
  test('离开名称输入框就向网关查询', async () => {
    render(<AgentComputeNewPage />);
    await typeName('hello_writer');

    await waitFor(() =>
      expect(mockListDefinitions.mock.calls.some((call) => call[1]?.name === 'hello_writer')).toBe(true),
    );
  });

  test('查到的同名定义会提示，而不是等到提交才 409', async () => {
    mockListDefinitions.mockImplementation((_tenant: string, query: { name?: string }) =>
      Promise.resolve(
        query?.name === 'hello_writer'
          ? { items: [{ definition_id: 'def-1', name: 'hello_writer' }], total: 1, page: 1, page_size: 9 }
          : { items: [], total: 0, page: 1, page_size: 9 },
      ),
    );

    render(<AgentComputeNewPage />);
    await typeName('hello_writer');

    await waitFor(() => expect(screen.getByText(/定义库中已有同名定义/)).toBeInTheDocument());
  });

  test('查询按名称精确匹配，包含该名字的不算冲突', async () => {
    mockListDefinitions.mockImplementation((_tenant: string, query: { name?: string }) =>
      Promise.resolve({
        items: query?.name === 'writer' ? [{ definition_id: 'def-2', name: 'hello_writer' }] : [],
        total: 1,
        page: 1,
        page_size: 9,
      }),
    );

    render(<AgentComputeNewPage />);
    await typeName('writer');

    await waitFor(() =>
      expect(mockListDefinitions.mock.calls.some((call) => call[1]?.name === 'writer')).toBe(true),
    );
    // The gateway's filter is a LIKE; only an exact name is a collision.
    expect(screen.queryByText(/定义库中已有同名定义/)).not.toBeInTheDocument();
  });
});

describe('填入示例任务', () => {
  test('已存在的名字会变成定义引用，即使它不在已加载的那一页', async () => {
    // The page already loaded holds nothing; only a by-name lookup finds it.
    mockListDefinitions.mockImplementation((_tenant: string, query: { name?: string }) =>
      Promise.resolve(
        query?.name === 'hello_writer'
          ? {
              items: [
                {
                  definition_id: 'def-9',
                  definition_row_id: 9,
                  name: 'hello_writer',
                  role: '产出 hello',
                  instructions: '只回复 hello',
                  kind: 'bot',
                  bot_type: 'personal',
                  manifest_document: '',
                  manifest_digest: '',
                  owner_id: '49912',
                  creator_id: '49912',
                  modifier_id: '49912',
                  created_at: 0,
                },
              ],
              total: 1,
              page: 1,
              page_size: 9,
            }
          : { items: [], total: 0, page: 1, page_size: 9 },
      ),
    );

    render(<AgentComputeNewPage />);
    fireEvent.click(await screen.findByRole('button', { name: '填入示例任务' }));

    // Resolved to the stored definition, so the roster shows a reference rather
    // than a blank entry that would collide on submit.
    await waitFor(() =>
      expect(mockListDefinitions.mock.calls.some((call) => call[1]?.name === 'hello_writer')).toBe(true),
    );
    await waitFor(() =>
      expect(screen.queryByText(/定义库中已有同名定义/)).not.toBeInTheDocument(),
    );
    expect(document.querySelector('input[id^="compute-name-"]')).toHaveValue('hello_writer');
  });
});