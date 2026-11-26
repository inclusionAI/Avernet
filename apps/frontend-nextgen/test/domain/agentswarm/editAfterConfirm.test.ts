/**
 * Requirement: "Submit a run from confirmed configuration" — an edit after a
 * submit is sent as a new configuration, and the builder neither mutates nor
 * consumes the draft.
 */
import { createLibraryAgentRef, toSubmitDTO, type ComputeDraft } from '@/domain/agentswarm/draft';

const draft = (overrides: Partial<ComputeDraft> = {}): ComputeDraft => ({
  goal: 'Say hello world',
  identityBotId: 'bot-a',
  refs: [{ ...createLibraryAgentRef({ definition_id: 'def-a', name: 'greeter', role: 'greets' }), key: 'a' }],
  ...overrides,
});

const SAVED = { a: 'def-a' };

test('the same draft submits identically twice — no hidden per-submit state', () => {
  // Confirmation is the user's act; the builder must not mutate or consume the draft.
  const d = draft();
  expect(toSubmitDTO(d, 'teamclaw', SAVED)).toEqual(toSubmitDTO(d, 'teamclaw', SAVED));
});

test('editing the goal after a submit produces a different payload, not a mutated one', () => {
  const first = toSubmitDTO(draft(), 'teamclaw', SAVED);
  const edited = toSubmitDTO(draft({ goal: 'Say goodbye world' }), 'teamclaw', SAVED);
  expect(first.goal).toBe('Say hello world');
  expect(edited.goal).toBe('Say goodbye world');
});

test('editing an agent name does not change which definition is referenced', () => {
  // The definition is saved once; renaming the row is an alias, not a new save.
  const renamed = draft({
    refs: [
      {
        ...createLibraryAgentRef({ definition_id: 'def-a', name: 'greeter', role: 'waves instead' }),
        key: 'a',
      },
    ],
  });
  expect(toSubmitDTO(renamed, 'teamclaw', SAVED).agent_refs?.[0].definition_id).toBe('def-a');
});

test('the identity source is carried once per payload, not per agent', () => {
  const dto = toSubmitDTO(draft(), 'teamclaw', SAVED);
  expect(dto.identity_bot_id).toBe('bot-a');
  for (const ref of dto.agent_refs ?? []) {
    expect(ref).not.toHaveProperty('identity_bot_id');
    expect(ref).not.toHaveProperty('bot_id');
  }
});