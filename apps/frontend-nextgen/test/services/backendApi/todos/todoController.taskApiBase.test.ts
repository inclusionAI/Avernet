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
      expect(TODO_ENDPOINTS.spaces).toBe('/api/v1/collaboration/tasks/spaces');
      expect(TODO_ENDPOINTS.spaceMembers('space/1')).toBe(
        '/api/v1/collaboration/tasks/spaces/space%2F1/members',
      );
      expect(TODO_ENDPOINTS.bots).toBe('/api/v1/collaboration/tasks/bots');
      expect(TODO_ENDPOINTS.list).toBe('/api/v1/collaboration/tasks/todos');
      expect(TODO_ENDPOINTS.run('todo/1')).toBe('/api/v1/collaboration/tasks/todos/todo%2F1/run');
      expect(TODO_ENDPOINTS.dimaProjects).toBe('/api/v1/collaboration/tasks/todos/sources/dima/projects');
      expect(TODO_ENDPOINTS.dimaWorkItems('P/1')).toBe(
        '/api/v1/collaboration/tasks/todos/sources/dima/projects/P%2F1/work-items',
      );
      expect(TODO_ENDPOINTS.dimaWorkItem('P/1', 'work/1')).toBe(
        '/api/v1/collaboration/tasks/todos/sources/dima/projects/P%2F1/work-items/work%2F1',
      );
      expect(TODO_ENDPOINTS.dimaAutoImportRules).toBe(
        '/api/v1/collaboration/tasks/todos/sources/dima/auto-import-rules',
      );
      expect(TODO_ENDPOINTS.dimaAutoImportRule('rule/1')).toBe(
        '/api/v1/collaboration/tasks/todos/sources/dima/auto-import-rules/rule%2F1',
      );
      expect(TODO_ENDPOINTS.botTeams).toBe('/api/v1/collaboration/tasks/bot-teams');
      expect(TODO_ENDPOINTS.botTeam('team/1')).toBe('/api/v1/collaboration/tasks/bot-teams/team%2F1');
    });
  });

  test('all Todo endpoints use the Engine internal API instead of OpenAPI', () => {
    Object.defineProperty(globalThis, 'TEAMCLAW_TASK_API_BASE', {
      configurable: true,
      value: '/openapi/v1/collaboration/tasks',
    });

    jest.isolateModules(() => {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const { TODO_ENDPOINTS } = require('@/services/backendApi/todos/todoController');
      expect(TODO_ENDPOINTS.spaces).toBe('/api/v1/collaboration/tasks/spaces');
      expect(TODO_ENDPOINTS.spaceMembers('1')).toBe('/api/v1/collaboration/tasks/spaces/1/members');
      expect(TODO_ENDPOINTS.bots).toBe('/api/v1/collaboration/tasks/bots');
      expect(TODO_ENDPOINTS.list).toBe('/api/v1/collaboration/tasks/todos');
      expect(TODO_ENDPOINTS.botTeams).toBe('/api/v1/collaboration/tasks/bot-teams');
      expect(TODO_ENDPOINTS.dimaAutoImportRules).toBe(
        '/api/v1/collaboration/tasks/todos/sources/dima/auto-import-rules',
      );
      expect(Object.values(TODO_ENDPOINTS).filter((endpoint) => typeof endpoint === 'string')).not.toEqual(
        expect.arrayContaining([expect.stringContaining('/openapi/')]),
      );
    });
  });
});
