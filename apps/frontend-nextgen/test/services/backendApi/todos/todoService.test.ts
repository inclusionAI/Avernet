import { listTodoArtifacts } from '@/services/backendApi/todos/todoController';
import { loadTodoArtifacts } from '@/services/backendApi/todos/todoService';

jest.mock('@/services/backendApi/todos/todoController', () => ({
  listTodoArtifacts: jest.fn(),
}));

const listArtifacts = jest.mocked(listTodoArtifacts);

describe('todoService artifacts', () => {
  beforeEach(() => {
    listArtifacts.mockReset();
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
