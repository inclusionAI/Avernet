describe('todoController task API path', () => {
  afterEach(() => {
    Reflect.deleteProperty(globalThis, 'TEAMCLAW_TASK_API_BASE');
    jest.resetModules();
  });

  test('uses the internal Task Engine path in local direct-Engine mode', () => {
    Object.defineProperty(globalThis, 'TEAMCLAW_TASK_API_BASE', {
      configurable: true,
      value: '/api/v1/collaboration/tasks',
    });

    jest.isolateModules(() => {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const { TODO_ENDPOINTS } = require('@/services/backendApi/todos/todoController');
      expect(TODO_ENDPOINTS.list).toBe('/api/v1/collaboration/tasks/todos');
      expect(TODO_ENDPOINTS.run('todo/1')).toBe('/api/v1/collaboration/tasks/todos/todo%2F1/run');
      expect(TODO_ENDPOINTS.dimaProjects).toBe('/api/v1/collaboration/tasks/todos/sources/dima/projects');
      expect(TODO_ENDPOINTS.dimaWorkItems('P/1')).toBe(
        '/api/v1/collaboration/tasks/todos/sources/dima/projects/P%2F1/work-items',
      );
      expect(TODO_ENDPOINTS.dimaWorkItem('P/1', 'work/1')).toBe(
        '/api/v1/collaboration/tasks/todos/sources/dima/projects/P%2F1/work-items/work%2F1',
      );
    });
  });
});
