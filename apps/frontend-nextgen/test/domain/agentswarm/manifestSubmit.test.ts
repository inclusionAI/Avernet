import { canSubmit, createAgentDraft, toSubmitDTO, validateDraft, type ComputeDraft } from '@/domain/agentswarm/draft';
import { MANIFEST_TEMPLATE } from '@/domain/agentswarm/manifest';

const bot = (partial: Parameters<typeof createAgentDraft>[0] = {}) =>
  createAgentDraft({ key: 'a', name: 'greeter', role: 'greets', kind: 'bot', ...partial });

const draft = (overrides: Partial<ComputeDraft> = {}): ComputeDraft => ({
  goal: 'g',
  agents: [bot()],
  identityBotId: 'bot-1',
  ...overrides,
});

test('a blank manifest is omitted from the payload rather than sent as an empty string', () => {
  const dto = toSubmitDTO(draft({ agents: [bot({ config_manifest: '   ' })] }), 'acme');
  expect(dto.agents[0]).not.toHaveProperty('config_manifest');
});

test('a real manifest is transmitted verbatim so the platform judges the grammar', () => {
  const dto = toSubmitDTO(draft({ agents: [bot({ config_manifest: MANIFEST_TEMPLATE })] }), 'acme');
  expect(dto.agents[0].config_manifest).toBe(MANIFEST_TEMPLATE);
  expect(dto.agents[0].config_manifest).toContain('cli_tools');
});

test('a valid manifest does not block submission', () => {
  expect(validateDraft(draft({ agents: [bot({ config_manifest: MANIFEST_TEMPLATE })] }))).toEqual([]);
});

test('an invalid manifest blocks submission and names the agent', () => {
  const issues = validateDraft(draft({ agents: [bot({ name: 'scout', config_manifest: 'manifest: [oops' })] }));
  expect(issues).toHaveLength(1);
  expect(issues[0].field).toBe('agents');
  expect(issues[0].message).toContain('scout');
  expect(canSubmit(draft({ agents: [bot({ config_manifest: 'manifest: [oops' })] }))).toBe(false);
});

test('one agent with a bad manifest blocks the whole run', () => {
  const issues = validateDraft(
    draft({
      agents: [bot({ key: 'ok', name: 'ok' }), bot({ key: 'bad', name: 'bad', config_manifest: 'x: [' })],
    }),
  );
  expect(issues.some((issue) => issue.message.includes('bad'))).toBe(true);
});

test('a manifest on one agent does not leak onto another', () => {
  const dto = toSubmitDTO(
    draft({
      agents: [bot({ key: 'a', config_manifest: MANIFEST_TEMPLATE }), bot({ key: 'b', name: 'plain' })],
    }),
    'acme',
  );
  expect(dto.agents[0].config_manifest).toBe(MANIFEST_TEMPLATE);
  expect(dto.agents[1]).not.toHaveProperty('config_manifest');
});