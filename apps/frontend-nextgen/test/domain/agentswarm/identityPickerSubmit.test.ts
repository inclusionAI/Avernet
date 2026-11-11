import { canSubmit, createAgentDraft, toSubmitDTO, type ComputeDraft } from '@/domain/agentswarm/draft';
import { toIdentityOptions } from '@/domain/agentswarm/identityOptions';
import type { ChatBotView } from '@/services/workspace/botSessionService';

const botAgent = (key: string) => createAgentDraft({ key, name: `worker-${key}`, role: 'works', kind: 'bot' });
const llmAgent = createAgentDraft({ key: 'l', name: 'planner', role: 'plans', kind: 'llm' });

const ownedBot = (realBotId: string): ChatBotView =>
  ({ botId: realBotId, realBotId, displayName: realBotId, online: true, chatable: true, status: 'ACTIVE' }) as ChatBotView;

const draft = (partial: Partial<ComputeDraft> = {}): ComputeDraft => ({
  goal: '批量处理',
  agents: [botAgent('1')],
  identityBotId: 'b1',
  ...partial,
});

test('a two-bot-agent configuration submits one run-level identity and no per-agent identity', () => {
  const dto = toSubmitDTO(draft({ agents: [botAgent('1'), botAgent('2')] }), 'acme');
  expect(dto.identity_bot_id).toBe('b1');
  expect(dto.agents).toHaveLength(2);
  for (const agent of dto.agents) {
    expect(agent).not.toHaveProperty('identity_bot_id');
    expect(agent).not.toHaveProperty('bot_id');
  }
});

test('selecting an identity makes a bot-kind draft submittable end to end', () => {
  const options = toIdentityOptions([ownedBot('b1'), ownedBot('b2')]);
  const selected = options.find((option) => option.selectable)!;
  const built = draft({ identityBotId: selected.botId });
  expect(canSubmit(built)).toBe(true);
  expect(canSubmit({ ...built, identityBotId: undefined })).toBe(false);
  expect(toSubmitDTO(built, 'acme').identity_bot_id).toBe(selected.botId);
});

test('the picker is irrelevant to an llm-only run and that run submits without one', () => {
  const built = draft({ agents: [llmAgent], identityBotId: undefined });
  expect(canSubmit(built)).toBe(true);
  expect(toSubmitDTO(built, 'acme')).not.toHaveProperty('identity_bot_id');
});

test('changing the selection changes the submitted identity, not the agents', () => {
  const a = toSubmitDTO(draft({ identityBotId: 'b1' }), 'acme');
  const b = toSubmitDTO(draft({ identityBotId: 'b2' }), 'acme');
  expect(a.identity_bot_id).toBe('b1');
  expect(b.identity_bot_id).toBe('b2');
  expect(a.agents).toEqual(b.agents);
});