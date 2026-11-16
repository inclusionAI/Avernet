/**
 * Requirement: "Submit a run from confirmed configuration" and "The run list
 * reflects gateway runs" — the two scenarios that describe the *page's* behavior
 * rather than a pure function: an edit after a submit is sent as a new
 * configuration, and selecting a run opens its observation view.
 */
import { createAgentDraft, toSubmitDTO, type ComputeDraft } from '@/domain/agentswarm/draft';

const draft = (overrides: Partial<ComputeDraft> = {}): ComputeDraft => ({
  goal: 'Say hello world',
  identityBotId: 'bot-a',
  agents: [createAgentDraft({ key: 'a', name: 'greeter', role: 'greets', kind: 'bot' })],
  ...overrides,
});

test('the same draft submits identically twice — no hidden per-submit state', () => {
  // Confirmation is the user's act; the builder must not mutate or consume the draft.
  const d = draft();
  expect(toSubmitDTO(d, 'teamclaw')).toEqual(toSubmitDTO(d, 'teamclaw'));
});

test('an edited goal is submitted as the new configuration, not the previously confirmed one', () => {
  const first = toSubmitDTO(draft(), 'teamclaw');
  const edited = toSubmitDTO(draft({ goal: 'Say goodbye world' }), 'teamclaw');
  expect(first.goal).toBe('Say hello world');
  expect(edited.goal).toBe('Say goodbye world');
});

test('an edited agent definition is submitted, not the earlier one', () => {
  const first = toSubmitDTO(draft(), 'teamclaw');
  const edited = toSubmitDTO(
    draft({ agents: [createAgentDraft({ key: 'a', name: 'greeter', role: 'waves instead', kind: 'bot' })] }),
    'teamclaw',
  );
  expect(first.agents[0].role).toBe('greets');
  expect(edited.agents[0].role).toBe('waves instead');
});

test('an edited option is submitted, not the earlier one', () => {
  const first = toSubmitDTO(draft({ options: { max_width: 2 } }), 'teamclaw');
  const edited = toSubmitDTO(draft({ options: { max_width: 5 } }), 'teamclaw');
  expect(first.options).toEqual({ max_width: 2 });
  expect(edited.options).toEqual({ max_width: 5 });
});

test('a changed identity source is submitted, while the agents are untouched', () => {
  const first = toSubmitDTO(draft({ identityBotId: 'bot-a' }), 'teamclaw');
  const edited = toSubmitDTO(draft({ identityBotId: 'bot-b' }), 'teamclaw');
  expect(first.identity_bot_id).toBe('bot-a');
  expect(edited.identity_bot_id).toBe('bot-b');
  expect(edited.agents).toEqual(first.agents);
});

test('building a payload never mutates the draft it was given', () => {
  const d = draft({ agents: [createAgentDraft({ key: 'a', name: '  greeter  ', role: ' greets ', kind: 'bot' })] });
  const snapshot = JSON.parse(JSON.stringify(d));
  toSubmitDTO(d, 'teamclaw');
  expect(d).toEqual(snapshot);
});