import type { BotCapabilitySet } from '@/domain/botEditor';
import { botEditorController } from '@/services/backendApi/bots/botEditorController';
import { botEditorService } from '@/services/botWorkshop/botEditorService';

jest.mock('@/services/backendApi/bots/botEditorController', () => ({
  botEditorController: {
    setSkillActive: jest.fn(), deleteSkill: jest.fn(), uploadSkill: jest.fn(), uploadSkillFolder: jest.fn(),
    createSkillSet: jest.fn(), updateSkillSet: jest.fn(), deleteSkillSet: jest.fn(), setSkillSetActive: jest.fn(),
    setSkillSetSkill: jest.fn(), createSkillCenterReferences: jest.fn(), listSkillCenterReferences: jest.fn(),
  },
}));

const controller = jest.mocked(botEditorController);
const set: BotCapabilitySet = { id: 'set-1', name: 'test', active: true, isDefault: false, skills: [], mcps: [], clis: [] };

afterEach(() => { jest.clearAllMocks(); jest.useRealTimers(); });

test.each([undefined, 'owner-1'])('capability set creation and activation keep owner %s', async (owner) => {
  controller.createSkillSet.mockResolvedValue({ data: { id: 'set-1', name: 'test', is_default: false, is_active: false } });
  controller.setSkillSetActive.mockResolvedValue({ data: { id: 'set-1', name: 'test', is_default: false, is_active: true } });
  await botEditorService.createSkillSet('bot-1', 'test', owner);
  expect(controller.createSkillSet).toHaveBeenCalledWith('bot-1', { name: 'test' }, owner);
  expect(controller.setSkillSetActive).toHaveBeenCalledWith('bot-1', 'set-1', true, owner);
});

test('Skill operations forward the addressed owner to their controller', async () => {
  const skill = { id: 'skill-1', name: 'test', active: true };
  await botEditorService.toggleSkill('bot-1', skill, 'owner-1');
  await botEditorService.deleteSkill('bot-1', skill.id, 'owner-1');
  await botEditorService.setSkillSetSkill('bot-1', set.id, skill.id, true, 'owner-1');
  await botEditorService.setSkillSetSkill('bot-1', set.id, skill.id, false, 'owner-1');
  expect(controller.setSkillActive).toHaveBeenCalledWith('bot-1', skill.id, false, 'owner-1');
  expect(controller.deleteSkill).toHaveBeenCalledWith('bot-1', skill.id, 'owner-1');
  expect(controller.setSkillSetSkill).toHaveBeenNthCalledWith(1, 'bot-1', set.id, skill.id, true, 'owner-1');
  expect(controller.setSkillSetSkill).toHaveBeenNthCalledWith(2, 'bot-1', set.id, skill.id, false, 'owner-1');
});

test('SkillCenter creation and every poll retain owner, including pending work', async () => {
  jest.useFakeTimers();
  controller.createSkillCenterReferences.mockResolvedValue({ data: { request_id: 'request-1', reference_ids: ['ref-1'] } });
  controller.listSkillCenterReferences
    .mockResolvedValueOnce({ data: { items: [{ status: 'PROCESSING' }] } } as never)
    .mockResolvedValueOnce({ data: { items: [{ status: 'COMPLETED' }] } } as never);
  const pending = botEditorService.addSkillCenterReferences('bot-1', 'set-1', ['code-1', 'code-1'], 'owner-1');
  await jest.advanceTimersByTimeAsync(2_000);
  await pending;
  expect(controller.createSkillCenterReferences).toHaveBeenCalledWith('bot-1', 'set-1', ['code-1'], 'owner-1');
  expect(controller.listSkillCenterReferences).toHaveBeenCalledTimes(2);
  expect(controller.listSkillCenterReferences.mock.calls).toEqual([
    ['bot-1', 'set-1', 'request-1', 'owner-1'], ['bot-1', 'set-1', 'request-1', 'owner-1'],
  ]);
});
