describe('task API runtime path', () => {
  afterEach(() => {
    Reflect.deleteProperty(globalThis, 'TEAMCLAW_TASK_API_BASE');
    jest.resetModules();
  });

  test('defaults reads to the public OpenAPI path', () => {
    jest.isolateModules(() => {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const { TASK_READ_API_BASE } = require('@/services/tasks/taskConfig');
      expect(TASK_READ_API_BASE).toBe('/openapi/v1/collaboration/tasks');
    });
  });

  test('uses the internal API path when local direct-Engine mode is configured', () => {
    Object.defineProperty(globalThis, 'TEAMCLAW_TASK_API_BASE', {
      configurable: true,
      value: '/api/v1/collaboration/tasks',
    });

    jest.isolateModules(() => {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const { TASK_READ_API_BASE } = require('@/services/tasks/taskConfig');
      expect(TASK_READ_API_BASE).toBe('/api/v1/collaboration/tasks');
    });
  });
});
