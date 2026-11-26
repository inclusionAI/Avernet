import {
  canSubmit,
  createNewAgentRef,
  toSubmitDTO,
  type AgentRefDraft,
  type ComputeDraft,
} from '@/domain/agentswarm/draft';
import { toIdentityOptions } from '@/domain/agentswarm/identityOptions';
import type { ChatBotView } from '@/services/workspace/botSessionService';

const botRef = (key: string): AgentRefDraft =>
  createNewAgentRef({ key, name: `worker-${key}`, role: 'works', kind: 'bot' });
const llmRef = createNewAgentRef({ key: 'l', name: 'planner', role: 'plans', kind: 'llm' });

const ownedBot = (realBotId: string): ChatBotView =>
  ({ botId: realBotId, realBotId, displayName: realBotId, online: true, chatable: true, status: 'ACTIVE' }) as ChatBotView;

const draft = (partial: Partial<ComputeDraft> = {}): ComputeDraft => ({
  goal: '批量处理',
  refs: [botRef('1')],
  identityBotId: 'b1',
  ...partial,
});

test('a two-agent configuration submits one run-level identity and no per-agent identity', () => {
  const dto = toSubmitDTO(draft({ refs: [botRef('1'), botRef('2')] }), 'acme', { 1: 'd1', 2: 'd2' });
  expect(dto.identity_bot_id).toBe('b1');
  expect(dto.agent_refs).toHaveLength(2);
  for (const ref of dto.agent_refs ?? []) {
    expect(ref).not.toHaveProperty('identity_bot_id');
    expect(ref).not.toHaveProperty('bot_id');
  }
});

test('the selected identity is the one submitted', () => {
  const options = toIdentityOptions([ownedBot('bot-x')]);
  const selected = options.find((option) => option.selectable)!;
  const built = draft({ identityBotId: selected.botId });
  expect(toSubmitDTO(built, 'acme', { 1: 'd1' }).identity_bot_id).toBe(selected.botId);
  // Clearing it blocks submission rather than sending an empty source.
  expect(canSubmit({ ...built, identityBotId: undefined })).toBe(false);
});

test('an llm-only roster needs no identity and sends none', () => {
  const dto = toSubmitDTO(draft({ refs: [llmRef], identityBotId: null }), 'acme', { l: 'd-l' });
  expect(dto).not.toHaveProperty('identity_bot_id');
});