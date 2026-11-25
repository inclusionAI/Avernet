import {
  canSubmit,
  createAgentDraft,
  hasBotAgent,
  IDENTITY_REQUIRED_MESSAGE,
  toSubmitDTO,
  validateDraft,
  type ComputeDraft,
} from '@/domain/agentswarm/draft';

const bot = createAgentDraft({ key: 'a', name: 'worker', role: 'works', kind: 'bot' });
const llm = createAgentDraft({ key: 'b', name: 'planner', role: 'plans', kind: 'llm' });

const draft = (partial: Partial<ComputeDraft> = {}): ComputeDraft => ({
  goal: '整理这批数据',
  agents: [bot],
  identityBotId: 'bot-1',
  ...partial,
});

test('bot-kind with an identity source is submittable', () => {
  expect(validateDraft(draft())).toEqual([]);
  expect(canSubmit(draft())).toBe(true);
});

test('bot-kind without an identity source is refused', () => {
  const issues = validateDraft(draft({ identityBotId: null }));
  expect(issues).toContainEqual({ field: 'identity', message: IDENTITY_REQUIRED_MESSAGE });
  expect(canSubmit(draft({ identityBotId: null }))).toBe(false);
});

test('a blank identity source counts as absent', () => {
  expect(canSubmit(draft({ identityBotId: '   ' }))).toBe(false);
});

test('llm-only without an identity source is exempt', () => {
  expect(validateDraft(draft({ agents: [llm], identityBotId: null }))).toEqual([]);
  expect(canSubmit(draft({ agents: [llm], identityBotId: null }))).toBe(true);
});

test('one bot agent among llm agents still requires a source', () => {
  expect(hasBotAgent([llm, bot])).toBe(true);
  expect(canSubmit(draft({ agents: [llm, bot], identityBotId: '' }))).toBe(false);
});

test('an empty goal or an unnamed agent is refused', () => {
  expect(validateDraft(draft({ goal: '  ' }))).toContainEqual({ field: 'goal', message: '请填写工作目标' });
  expect(validateDraft(draft({ agents: [] }))).toContainEqual({
    field: 'agents',
    message: '请至少定义一个执行 Agent',
  });
  expect(validateDraft(draft({ agents: [{ ...bot, name: ' ' }] }))).toContainEqual({
    field: 'agents',
    message: '每个 Agent 都需要名称',
  });
});

test('the submit payload carries the confirmed goal and agents unchanged', () => {
  const dto = toSubmitDTO(draft({ options: { max_width: 3 } }), 'acme');
  expect(dto).toMatchObject({
    tenant: 'acme',
    goal: '整理这批数据',
    identity_bot_id: 'bot-1',
    options: { max_width: 3 },
  });
  expect(dto.agents).toEqual([
    { name: 'worker', role: 'works', kind: 'bot', engine: 'openclaw', bot_type: 'personal' },
  ]);
});

test('one run-level identity is submitted for a two-bot configuration', () => {
  const dto = toSubmitDTO(
    draft({ agents: [bot, createAgentDraft({ key: 'c', name: 'w2', role: 'works', kind: 'bot' })] }),
    'acme',
  );
  expect(dto.identity_bot_id).toBe('bot-1');
  // No agent carries an identity field of its own.
  for (const agent of dto.agents) expect(agent).not.toHaveProperty('identity_bot_id');
});

test('an llm-only payload omits the identity field rather than sending an empty one', () => {
  const dto = toSubmitDTO(draft({ agents: [llm], identityBotId: null }), 'acme');
  expect(dto).not.toHaveProperty('identity_bot_id');
});

test('draft-only fields never leak into the wire payload', () => {
  const dto = toSubmitDTO(draft({ agents: [{ ...bot, key: 'internal-key' }] }), 'acme');
  expect(dto.agents[0]).not.toHaveProperty('key');
});

test('empty optional agent fields are omitted so extra=forbid stays satisfied', () => {
  const dto = toSubmitDTO(draft({ agents: [{ ...bot, model: null, instructions: '  ' }] }), 'acme');
  expect(dto.agents[0]).not.toHaveProperty('model');
  expect(dto.agents[0]).not.toHaveProperty('instructions');
});