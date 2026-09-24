import { executeTask } from '@/services/backendApi/tasks/taskController';
import type { TaskComposerContext, TaskComposerForm } from '@/services/tasks/taskMapper';
import { executeTaskService } from '@/services/tasks/taskService';
import { useTaskStore } from '@/stores/taskStore';

jest.mock('@/services/backendApi/tasks/taskController', () => ({
  executeTask: jest.fn(),
}));

const mockedExecuteTask = executeTask as unknown as jest.Mock;

const form: TaskComposerForm = {
  title: '接力任务',
  instruction: '完成需求实现',
  objective: '交付可运行功能',
  acceptances: ['测试通过'],
  taskType: 'dynamic',
};

const context: TaskComposerContext = {
  sourceType: 'bot',
  ownerUserId: 'user-1',
  ownerBotId: 'bot-main',
  mainSessionId: 'session-1',
};

function executeResponse(extendProps?: Record<string, unknown>) {
  return {
    code: 200000,
    message: 'OK',
    data: {
      task_id: 'task-1',
      success: true,
      run_id: 1,
      message: null,
      ...(extendProps ? { extend_props: extendProps } : {}),
    },
    request_id: 'request-1',
  };
}

describe('executeTaskService orchestration mode mapping', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    useTaskStore.getState().reset();
  });

  it('execute 请求和本地领域记录都使用 context + goal，不生成 metadata', async () => {
    mockedExecuteTask.mockResolvedValue(executeResponse());

    const record = await executeTaskService({ form, ctx: context });
    const request = mockedExecuteTask.mock.calls[0][0];

    expect(request.task_spec).not.toHaveProperty('metadata');
    expect(request.task_spec.context.title).toBe('接力任务');
    expect(request.task_spec.context.extend_props).toEqual({
      deliverables: [],
      constraints: [],
      resources: [],
    });
    expect(record.task_info.task_spec).not.toHaveProperty('metadata');
    expect(record.task_info.task_spec.context.title).toBe('接力任务');
    expect(record.task_info.execution_config.main_session_id).toBe('session-1');
  });

  it('旧响应未返回模式时保持原有中心化领域记录', async () => {
    mockedExecuteTask.mockResolvedValue(executeResponse());

    const record = await executeTaskService({ form, ctx: context });

    expect(record.task_info.execution_config.orchestration_mode).toBeUndefined();
    expect(record.task_info.execution_config.root_node_id).toBeUndefined();
    expect(mockedExecuteTask.mock.calls[0][0].execution_config).not.toHaveProperty('orchestration_mode');
    expect(mockedExecuteTask.mock.calls[0][0].execution_config).not.toHaveProperty('root_node_id');
  });

  it('relay 响应把后端盖章的模式和根节点映射到领域记录', async () => {
    mockedExecuteTask.mockResolvedValue(executeResponse({ orchestration_mode: 'relay', root_node_id: 'root-1' }));

    const record = await executeTaskService({ form, ctx: context });

    expect(record.task_info.execution_config).toEqual(
      expect.objectContaining({ orchestration_mode: 'relay', root_node_id: 'root-1' }),
    );
    expect(useTaskStore.getState().lastTaskRecord).toBe(record);
  });

  it('centralized 响应不引入 relay 分支字段', async () => {
    mockedExecuteTask.mockResolvedValue(executeResponse({ orchestration_mode: 'centralized', root_node_id: 'task-1' }));

    const record = await executeTaskService({ form, ctx: context });

    expect(record.task_info.execution_config.orchestration_mode).toBeUndefined();
    expect(record.task_info.execution_config.root_node_id).toBeUndefined();
  });
});
