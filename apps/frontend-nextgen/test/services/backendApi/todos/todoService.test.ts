import {
  getTodoSourceDraft,
  listTodoArtifacts,
  listTodoSourceProjects,
  listTodoSourceWorkItems,
} from '@/services/backendApi/todos/todoController';
import {
  loadTodoArtifacts,
  loadTodoSourceDraft,
  loadTodoSourceProjects,
  loadTodoSourceWorkItems,
} from '@/services/backendApi/todos/todoService';

jest.mock('@/services/backendApi/todos/todoController', () => ({
  listTodoArtifacts: jest.fn(),
  listTodoSourceProjects: jest.fn(),
  listTodoSourceWorkItems: jest.fn(),
  getTodoSourceDraft: jest.fn(),
}));

const listArtifacts = jest.mocked(listTodoArtifacts);
const listProjects = jest.mocked(listTodoSourceProjects);
const listWorkItems = jest.mocked(listTodoSourceWorkItems);
const getDraft = jest.mocked(getTodoSourceDraft);

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

describe('todoService Dima source normalization', () => {
  beforeEach(() => {
    listProjects.mockReset();
    listWorkItems.mockReset();
    getDraft.mockReset();
  });

  it('映射项目、工作项和可编辑草稿', async () => {
    listProjects.mockResolvedValue({
      code: 200000,
      data: [{ id: 1259, name: '支付项目' }],
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
      },
    });

    await expect(loadTodoSourceProjects()).resolves.toEqual([{ id: '1259', name: '支付项目' }]);
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
    });
  });
});
