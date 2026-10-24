import { botEditorController as controller } from '@/services/backendApi/bots/botEditorController';

jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getCurrentOpenApiUserId: () => ({ status: 'available', value: 'member-1' }),
  }),
}));

type Operation = (ownerId?: string) => Promise<unknown>;
const operations: Array<[string, Operation, string]> = [
  ['activate Skill', (owner) => controller.setSkillActive('bot-1', 'skill-1', true, owner), 'POST'],
  ['deactivate Skill', (owner) => controller.setSkillActive('bot-1', 'skill-1', false, owner), 'POST'],
  ['delete Skill', (owner) => controller.deleteSkill('bot-1', 'skill-1', owner), 'DELETE'],
  ['upload ZIP', (owner) => controller.uploadSkill('bot-1', new ArrayBuffer(0), owner), 'POST'],
  ['upload folder', (owner) => controller.uploadSkillFolder('bot-1', [], owner), 'POST'],
  ['create set', (owner) => controller.createSkillSet('bot-1', { name: 'test' }, owner), 'POST'],
  ['rename set', (owner) => controller.updateSkillSet('bot-1', 'set-1', { name: 'test' }, owner), 'PUT'],
  ['delete set', (owner) => controller.deleteSkillSet('bot-1', 'set-1', owner), 'DELETE'],
  ['activate set', (owner) => controller.setSkillSetActive('bot-1', 'set-1', true, owner), 'POST'],
  ['deactivate set', (owner) => controller.setSkillSetActive('bot-1', 'set-1', false, owner), 'POST'],
  ['add Skill', (owner) => controller.setSkillSetSkill('bot-1', 'set-1', 'skill-1', true, owner), 'PUT'],
  ['remove Skill', (owner) => controller.setSkillSetSkill('bot-1', 'set-1', 'skill-1', false, owner), 'DELETE'],
  ['reference SkillCenter', (owner) => controller.createSkillCenterReferences('bot-1', 'set-1', ['code'], owner), 'POST'],
  ['poll SkillCenter', (owner) => controller.listSkillCenterReferences('bot-1', 'set-1', 'request-1', owner), 'GET'],
];

afterEach(() => jest.restoreAllMocks());

describe.each([undefined, 'other-owner'])('Skill owner routing: %s', (ownerId) => {
  test.each(operations)('%s retains authenticated user and optional addressed owner', async (_name, operation, method) => {
    const fetcher = jest.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ code: 200000, data: null }),
    } as Response);
    await operation(ownerId);
    const [input, init] = fetcher.mock.calls[0];
    const url = new URL(String(input), 'http://localhost');
    expect(url.pathname).toMatch(/^\/openapi\/v1\/bots\/bot-1\//);
    expect(url.searchParams.get('user_id')).toBe('member-1');
    expect(url.searchParams.get('owner_id')).toBe(ownerId ?? null);
    expect(init?.method).toBe(method);
  });
});
