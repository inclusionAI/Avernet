import { botEditorController } from '@/services/backendApi/bots/botEditorController';
import { afterEach, describe, expect, jest, test } from '@jest/globals';

const ok = () =>
  Promise.resolve({
    ok: true,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({ code: 200000, data: null }),
    blob: async () => new Blob(),
  } as Response);
describe('botEditorController', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });
  test.each([
    [
      'skills',
      () => botEditorController.listSkills('bot-1'),
      '/openapi/v1/bots/bot-1/skills?page=1&page_size=100',
      'GET',
    ],
    [
      'resources',
      () => botEditorController.listResources('bot-1', 'docs'),
      '/openapi/v1/bots/bot-1/resources?path=docs&page=1&page_size=100',
      'GET',
    ],
    [
      'resource directory download',
      () => botEditorController.downloadResourceDirectory('bot-1', 'docs/spec'),
      '/openapi/v1/bots/bot-1/resources/download-dir?path=docs%2Fspec',
      'GET',
    ],
    [
      'routines',
      () => botEditorController.listRoutines('bot-1'),
      '/openapi/v1/bots/bot-1/routines?page=1&page_size=100',
      'GET',
    ],
    [
      'routine runs',
      () => botEditorController.listRoutineRuns('bot-1', 'routine-1'),
      '/openapi/v1/bots/bot-1/routines/routine-1/runs?page=1&page_size=20',
      'GET',
    ],
    ['identity files', () => botEditorController.listIdentityFiles('bot-1'), '/openapi/v1/bots/bot-1/identity', 'GET'],
    [
      'channels',
      () => botEditorController.listChannels('bot-1'),
      '/openapi/v1/bots/bot-1/channels?page=1&page_size=100&stage=draft',
      'GET',
    ],
    [
      'draft caller context',
      () => botEditorController.getCallerContext('bot-1'),
      '/openapi/v1/bots/bot-1/caller-context?stage=draft',
      'GET',
    ],
    [
      'engine config',
      () => botEditorController.getEngineConfig('bot-1'),
      '/openapi/v1/bots/bot-1/engine/config',
      'GET',
    ],
    [
      'engine status',
      () => botEditorController.getEngineStatus('bot-1'),
      '/openapi/v1/bots/bot-1/engine/status',
      'GET',
    ],
    [
      'approval',
      () => botEditorController.getApprovalConfig('bot-1'),
      '/openapi/v1/bots/bot-1/lifecycle/approval',
      'GET',
    ],
    ['bot MCPs', () => botEditorController.listBotMcps('bot-1'), '/openapi/v1/bots/bot-1/mcps', 'GET'],
    [
      'MCP market',
      () => botEditorController.listMcpServers(),
      '/openapi/v1/bots/mcp/servers?page=1&page_size=100',
      'GET',
    ],
    [
      'MCP permission',
      () => botEditorController.getMcpPermission('mcp.weather'),
      '/openapi/v1/bots/mcp/servers/mcp.weather/permissions',
      'GET',
    ],
    [
      'Skill market',
      () => botEditorController.listRepositorySkills(),
      '/openapi/v1/bots/skills/repository?page=1&page_size=100&sort=latest',
      'GET',
    ],
    [
      'Skill workshop',
      () => botEditorController.listConsumableSpaceSkills('12'),
      '/openapi/v1/bots/spaces/12/skills/consumable?page=1&page_size=100',
      'GET',
    ],
    [
      'render screens',
      () => botEditorController.listRenderScreens('bot-1'),
      '/openapi/v1/bots/bot-1/render-screens',
      'GET',
    ],
    ['service lifecycle', () => botEditorController.getLifecycle('bot-1'), '/openapi/v1/bots/bot-1/lifecycle', 'GET'],
    [
      'service edit lock',
      () => botEditorController.getEditLock('bot-1', 'owner-1'),
      '/openapi/v1/bots/bot-1/edit-lock?owner_id=owner-1',
      'GET',
    ],
    ['skill sets', () => botEditorController.listSkillSets('bot-1'), '/openapi/v1/bots/bot-1/skill-sets', 'GET'],
    [
      'skill set resources',
      () => botEditorController.listSkillSetResources('bot-1'),
      '/openapi/v1/bots/bot-1/skill-sets/resources',
      'GET',
    ],
    [
      'skill set members',
      () => botEditorController.listSkillSetSkills('bot-1', '7'),
      '/openapi/v1/bots/bot-1/skill-sets/7/skills',
      'GET',
    ],
  ])('%s uses the bot-addressed OpenAPI', async (_label, invoke, url, method) => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(ok);
    await invoke();
    expect(fetch).toHaveBeenCalledWith(url, expect.objectContaining({ method }));
  });

  it('render screen list passes owner_id for friend bots', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(ok);
    await botEditorController.listRenderScreens('bot-1', 'owner-1');
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot-1/render-screens?owner_id=owner-1',
      expect.objectContaining({ method: 'GET' }),
    );
  });
  test('upgrades a running service publication through OpenAPI', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(ok);
    await botEditorController.upgradeLifecycle('bot-1', 17);
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot-1/lifecycle/17/upgrade',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  test('steals a service Bot edit lock through OpenAPI', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(ok);
    await botEditorController.stealEditLock('bot-1', 'owner-1');
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot-1/edit-lock/steal?owner_id=owner-1',
      expect.objectContaining({ method: 'POST' }),
    );
  });
  test('updates approval through the lifecycle OpenAPI', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(ok);
    await botEditorController.updateApprovalConfig('bot-1', true);
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot-1/lifecycle/approval',
      expect.objectContaining({ method: 'PUT', body: JSON.stringify({ should_approval: true }) }),
    );
  });
  test('updates the MCP caller mode through OpenAPI', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(ok);
    await botEditorController.updateMcpCallType('bot-1', 'mcp/weather', 'caller');
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot-1/mcps/mcp%2Fweather/call-type',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ call_type: 'caller' }) }),
    );
  });
  test('advances a service publication through the lifecycle OpenAPI', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(ok);
    await botEditorController.advanceLifecycle('bot-1', 'prestable');
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot-1/lifecycle/advance',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ stage: 'prestable' }) }),
    );
  });
  test('uploads Skill ZIP as a raw body', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(ok);
    const body = new Uint8Array([1, 2, 3]).buffer;
    await botEditorController.uploadSkill('bot-1', body);
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot-1/skills',
      expect.objectContaining({
        method: 'POST',
        body,
        headers: expect.objectContaining({ 'Content-Type': 'application/zip' }),
      }),
    );
  });
  test('uploads a Skill directory as multipart files with relative paths', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(ok);
    const file = new File(['content'], 'SKILL.md', { type: 'text/markdown' });
    Object.defineProperty(file, 'webkitRelativePath', { value: 'demo/SKILL.md' });
    await botEditorController.uploadSkillFolder('bot-1', [file]);
    const [, init] = fetch.mock.calls[0];
    expect(fetch.mock.calls[0][0]).toBe('/openapi/v1/bots/bot-1/skills/upload-folder');
    expect(init).toEqual(expect.objectContaining({ method: 'POST', body: expect.any(FormData) }));
    expect((init?.body as FormData).get('file_paths')).toBe('["demo/SKILL.md"]');
  });
  test('writes render screen through Bot-addressed OpenAPI', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(ok);
    await botEditorController.createRenderScreen('bot-1', {
      name: 'overview',
      cdn_url: 'https://cdn.example/screen.js',
    });
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot-1/render-screens',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ name: 'overview', cdn_url: 'https://cdn.example/screen.js' }),
      }),
    );
  });
  test('creates a SkillSet with the required idempotency header', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(ok);
    await botEditorController.createSkillSet('bot-1', { name: '研发能力' });
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot-1/skill-sets',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ name: '研发能力' }),
        headers: expect.objectContaining({ 'Idempotency-Key': expect.any(String) }),
      }),
    );
  });
  test('adds an MCP to a SkillSet with PUT', async () => {
    const fetch = jest.spyOn(globalThis, 'fetch').mockImplementation(ok);
    await botEditorController.setSkillSetMcp('bot-1', '7', 'mcp.weather', true);
    expect(fetch).toHaveBeenCalledWith(
      '/openapi/v1/bots/bot-1/skill-sets/7/mcps/mcp.weather',
      expect.objectContaining({ method: 'PUT' }),
    );
  });
});
