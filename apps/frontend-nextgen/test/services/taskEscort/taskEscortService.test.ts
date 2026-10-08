import { listFacadeBindings, saveWorkflow } from '@/services/backendApi';
import { taskEscortService, WorkflowImportValidationError } from '@/services/taskEscort/taskEscortService';

jest.mock('@/services/backendApi', () => ({
  getFlowRun: jest.fn(),
  getWorkflow: jest.fn(),
  listFacadeBindings: jest.fn(),
  listFlowRuns: jest.fn(),
  listWorkflows: jest.fn(),
  listWorkflowTypes: jest.fn(),
  saveWorkflow: jest.fn(),
}));

const mockedListFacadeBindings = listFacadeBindings as jest.MockedFunction<typeof listFacadeBindings>;
const mockedSaveWorkflow = saveWorkflow as jest.MockedFunction<typeof saveWorkflow>;

const yaml = `id: risk-processor
version: "1"
title: 风险处理器
nodes: []
`;

describe('taskEscortService.createWorkflowFromYaml', () => {
  beforeEach(() => {
    mockedListFacadeBindings.mockReset();
    mockedSaveWorkflow.mockReset();
  });

  test('parses YAML, defaults command to workflow id, and saves in the current bot scope', async () => {
    mockedListFacadeBindings.mockResolvedValue([]);
    mockedSaveWorkflow.mockImplementation(async (request) => request.spec);

    const result = await taskEscortService.createWorkflowFromYaml(
      { yaml, remark: '稳定版', botOwnerId: 'space-1', botId: 'bot-1' },
      [],
    );

    expect(mockedSaveWorkflow).toHaveBeenCalledWith({
      workflowId: 'risk-processor',
      botOwnerId: 'space-1',
      botId: 'bot-1',
      spec: expect.objectContaining({
        id: 'risk-processor',
        title: '风险处理器',
        facade: { command: 'risk-processor', remark: '稳定版' },
      }),
      facade: { command: 'risk-processor', remark: '稳定版' },
    });
    expect(result.id).toBe('risk-processor');
  });

  test('rejects an existing workflow id before calling the backend', async () => {
    await expect(
      taskEscortService.createWorkflowFromYaml({ yaml }, [
        { workflowId: 'risk-processor', title: '已有工作流', packId: null, updatedAt: 1 },
      ]),
    ).rejects.toMatchObject<Partial<WorkflowImportValidationError>>({
      field: 'yaml',
      message: '工作流 ID "risk-processor" 已存在，请修改 id 后重试',
    });

    expect(mockedListFacadeBindings).not.toHaveBeenCalled();
    expect(mockedSaveWorkflow).not.toHaveBeenCalled();
  });

  test('rejects a command already bound to another workflow', async () => {
    mockedListFacadeBindings.mockResolvedValue([
      { command: 'risk-processor', workflowId: 'other-workflow', packId: null, remark: null },
    ]);

    await expect(taskEscortService.createWorkflowFromYaml({ yaml }, [])).rejects.toMatchObject<
      Partial<WorkflowImportValidationError>
    >({
      field: 'command',
      message: '命令 "/risk-processor" 已绑定到工作流 "other-workflow"',
    });
    expect(mockedSaveWorkflow).not.toHaveBeenCalled();
  });
});
