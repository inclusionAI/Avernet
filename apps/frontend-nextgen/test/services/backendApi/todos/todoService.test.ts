import {
  createTodoAutoImportRule,
  getTodoSourceDraft,
  listTodoArtifacts,
  listTodoAutoImportRules,
  listTodoSourceProjects,
  listTodoSpaces,
  listTodoSourceWorkItems,
  updateTodoAutoImportRule,
} from '@/services/backendApi/todos/todoController';
import {
  createTodoAutoImportRule as createTodoAutoImportRuleService,
  loadTodoArtifacts,
  loadTodoAutoImportRules,
  loadTodoSourceDraft,
  loadTodoSourceProjects,
  loadTodoSpaces,
  loadTodoSourceWorkItems,
  updateTodoAutoImportRule as updateTodoAutoImportRuleService,
} from '@/services/backendApi/todos/todoService';

jest.mock('@/services/backendApi/todos/todoController', () => {
  const actual = jest.requireActual('@/services/backendApi/todos/todoController');
  return {
    ...actual,
    createTodoAutoImportRule: jest.fn(),
    getTodoSourceDraft: jest.fn(),
    listTodoArtifacts: jest.fn(),
    listTodoAutoImportRules: jest.fn(),
    listTodoSourceProjects: jest.fn(),
    listTodoSpaces: jest.fn(),
    listTodoSourceWorkItems: jest.fn(),
    updateTodoAutoImportRule: jest.fn(),
  };
});

const listArtifacts = jest.mocked(listTodoArtifacts);
const listProjects = jest.mocked(listTodoSourceProjects);
const listSpaces = jest.mocked(listTodoSpaces);
const listWorkItems = jest.mocked(listTodoSourceWorkItems);
const getDraft = jest.mocked(getTodoSourceDraft);
const createAutoImportRule = jest.mocked(createTodoAutoImportRule);
const listAutoImportRules = jest.mocked(listTodoAutoImportRules);
const updateAutoImportRule = jest.mocked(updateTodoAutoImportRule);

describe('todoService artifacts', () => {
  beforeEach(() => {
    listArtifacts.mockReset();
    listProjects.mockReset();
    listWorkItems.mockReset();
    getDraft.mockReset();
  });

  it('保留后端使用 todo_execution_id 返回的产物', async () => {
    listArtifacts.mockResolvedValue({
      code: 200000,
      data: [
        {
          artifact_id: 'todoartifact-1',
          todo_id: 'todo-1',
          todo_execution_id: 'todoexecution-1',
          artifact_name: '执行结果',
          kind: 'text',
          content: { text: '三款产品的定价与功能矩阵' },
          created_at: '2026-11-15T10:00:00+08:00',
        },
      ],
    });

    await expect(loadTodoArtifacts('todo-1')).resolves.toEqual([
      {
        artifactId: 'todoartifact-1',
        todoId: 'todo-1',
        executionId: 'todoexecution-1',
        artifactName: '执行结果',
        kind: 'text',
        content: {
          text: '三款产品的定价与功能矩阵',
          isFinalArtifact: false,
        },
        createdAt: '2026-11-15T10:00:00+08:00',
      },
    ]);
  });

  it('兼容旧响应中的 execution_id', async () => {
    listArtifacts.mockResolvedValue({
      code: 200000,
      data: [
        {
          artifact_id: 'todoartifact-legacy',
          todo_id: 'todo-1',
          execution_id: 'todoexecution-legacy',
          artifact_name: '旧版链接产物',
          kind: 'url_link',
          content: { url: 'https://example.test/report' },
          created_at: '2026-11-15T10:01:00+08:00',
        },
      ],
    });

    await expect(loadTodoArtifacts('todo-1')).resolves.toEqual([
      expect.objectContaining({
        artifactId: 'todoartifact-legacy',
        executionId: 'todoexecution-legacy',
        kind: 'url_link',
      }),
    ]);
  });
});

describe('todoService Engine space normalization', () => {
  beforeEach(() => {
    listSpaces.mockReset();
  });

  it('映射 Engine 分页空间响应为 Todo 最小空间模型', async () => {
    listSpaces.mockResolvedValue({
      code: 200000,
      data: {
        items: [
          {
            space_id: 10,
            space_name: '测试用户的个人空间',
            space_type: 'PERSONAL',
          },
        ],
      },
    });

    await expect(loadTodoSpaces()).resolves.toEqual([
      {
        spaceId: 10,
        spaceName: '测试用户的个人空间',
        spaceType: 'PERSONAL',
      },
    ]);
  });

  it('兼容 Engine 的 id/name/type 与 spaces 列表结构', async () => {
    listSpaces.mockResolvedValue({
      code: 200000,
      data: {
        spaces: [{ id: '11', name: '协作空间', type: 'team' }],
      },
    });

    await expect(loadTodoSpaces()).resolves.toEqual([
      {
        spaceId: 11,
        spaceName: '协作空间',
        spaceType: 'TEAM',
      },
    ]);
  });
});

describe('todoService Dima source normalization', () => {
  beforeEach(() => {
    listProjects.mockReset();
    listWorkItems.mockReset();
    getDraft.mockReset();
  });

  it('映射项目、工作项和可编辑草稿', async () => {
    listProjects.mockResolvedValue({
      code: 200000,
      data: [{ id: 1259, name: '支付项目', kind: 'project' }],
    });
    listWorkItems.mockResolvedValue({
      code: 200000,
      data: [
        {
          id: '2026112000000000001',
          project_id: '1259',
          category: 'bug',
          title: '登录缺陷',
          status: '处理中',
          updated_at: '2026-11-20T12:00:00Z',
        },
      ],
    });
    getDraft.mockResolvedValue({
      code: 200000,
      data: {
        source_type: 'dima',
        project_id: '1259',
        work_item_id: '2026112000000000001',
        category: 'bug',
        title: '登录缺陷',
        description: '整理后的内容',
        source_url: 'https://dima.example/items/1',
        metadata: {
          processor_id: '149844',
          processor_name: '张三',
        },
      },
    });

    await expect(loadTodoSourceProjects()).resolves.toEqual([{ id: '1259', name: '支付项目', kind: 'project' }]);
    await expect(loadTodoSourceWorkItems('1259')).resolves.toEqual([
      {
        id: '2026112000000000001',
        projectId: '1259',
        category: 'bug',
        title: '登录缺陷',
        status: '处理中',
        updatedAt: '2026-11-20T12:00:00Z',
      },
    ]);
    await expect(loadTodoSourceDraft('1259', '2026112000000000001')).resolves.toEqual({
      sourceType: 'dima',
      projectId: '1259',
      workItemId: '2026112000000000001',
      category: 'bug',
      title: '登录缺陷',
      description: '整理后的内容',
      sourceUrl: 'https://dima.example/items/1',
      sourceUpdatedAt: undefined,
      metadata: {
        processor_id: '149844',
        processor_name: '张三',
      },
    });
  });
});

describe('todoService Dima auto import rule normalization', () => {
  beforeEach(() => {
    createAutoImportRule.mockReset();
    listAutoImportRules.mockReset();
    updateAutoImportRule.mockReset();
  });

  const ruleDto = {
    rule_id: 'rule-1',
    space_id: 10,
    source_container_ids: ['W1', 'P1'],
    filter_text: '只导入需要研发处理且尚未完成的事项',
    bot_id: 'bot-owned:u-1',
    status: 'enabled',
    cursor_updated_at: '2026-11-25T08:00:00+00:00',
    created_at: '2026-11-25T07:00:00+00:00',
    updated_at: '2026-11-25T08:00:00+00:00',
  };

  const rule = {
    ruleId: 'rule-1',
    spaceId: 10,
    sourceContainerIds: ['W1', 'P1'],
    filterText: '只导入需要研发处理且尚未完成的事项',
    botId: 'bot-owned:u-1',
    status: 'enabled' as const,
    cursorUpdatedAt: '2026-11-25T08:00:00+00:00',
    createdAt: '2026-11-25T07:00:00+00:00',
    updatedAt: '2026-11-25T08:00:00+00:00',
  };

  it('映射创建和查询响应中的 snake_case 字段', async () => {
    createAutoImportRule.mockResolvedValue({ code: 200000, data: ruleDto });
    listAutoImportRules.mockResolvedValue({ code: 200000, data: [ruleDto] });

    await expect(
      createTodoAutoImportRuleService({
        spaceId: 10,
        sourceContainerIds: ['W1', 'P1'],
        filterText: '只导入需要研发处理且尚未完成的事项',
        botId: 'bot-owned:u-1',
      }),
    ).resolves.toEqual(rule);
    await expect(loadTodoAutoImportRules(10)).resolves.toEqual([rule]);
  });

  it('映射规则更新结果', async () => {
    updateAutoImportRule.mockResolvedValue({
      code: 200000,
      data: { ...ruleDto, status: 'disabled', cursor_updated_at: null },
    });

    await expect(updateTodoAutoImportRuleService('rule-1', { status: 'disabled' })).resolves.toEqual({
      ...rule,
      status: 'disabled',
      cursorUpdatedAt: undefined,
    });
  });
});
