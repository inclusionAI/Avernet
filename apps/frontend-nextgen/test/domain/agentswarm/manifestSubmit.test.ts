import {
  canSubmit,
  createNewAgentRef,
  resolveRosterRefs,
  toSubmitDTO,
  validateDraft,
  type ComputeDraft,
  type AgentRefDraft,
} from '@/domain/agentswarm/draft';
import { MANIFEST_TEMPLATE } from '@/domain/agentswarm/manifest';

const bot = (partial: Parameters<typeof createNewAgentRef>[0] = {}) =>
  createNewAgentRef({ key: 'a', name: 'greeter', role: 'greets', kind: 'bot', ...partial });

const draft = (overrides: Partial<ComputeDraft> = {}): ComputeDraft => ({
  goal: 'g',
  refs: [bot()],
  identityBotId: 'bot-1',
  ...overrides,
});

/**
 * The manifest now travels in the *definition*, not the run payload, so these
 * assert on what is handed to the save call — that is where the platform's
 * `extra="forbid"` and manifest validation actually apply.
 */
const captureSave = () => {
  const calls: Array<{ entry: AgentRefDraft; name: string }> = [];
  const save = jest.fn(async (entry: AgentRefDraft, name: string) => {
    calls.push({ entry, name });
    return `def-${name}`;
  });
  return { save, calls };
};

test('a blank manifest reaches the saved definition as an empty document, never as junk', async () => {
  const { save, calls } = captureSave();
  await resolveRosterRefs(draft({ refs: [bot({ config_manifest: '   ' })] }), save);
  expect(calls[0].entry.body.config_manifest?.trim() ?? '').toBe('');
});

test('a real manifest is carried to the definition verbatim', async () => {
  const { save, calls } = captureSave();
  await resolveRosterRefs(draft({ refs: [bot({ config_manifest: MANIFEST_TEMPLATE })] }), save);
  expect(calls[0].entry.body.config_manifest).toBe(MANIFEST_TEMPLATE);
});

test('an unparseable manifest is refused before any save happens', () => {
  const issues = validateDraft(draft({ refs: [bot({ config_manifest: 'manifest: [' })] }));
  expect(issues.some((issue) => issue.message.includes('配置清单无法提交'))).toBe(true);
  expect(canSubmit(draft({ refs: [bot({ config_manifest: 'manifest: [' })] }))).toBe(false);
});

test('a two-agent roster still submits one run-level identity', () => {
  const dto = toSubmitDTO(
    draft({ refs: [bot({ key: 'a' }), bot({ key: 'b', name: 'reviewer' })] }),
    'acme',
    { a: 'def-a', b: 'def-b' },
  );
  expect(dto.identity_bot_id).toBe('bot-1');
  expect(dto.agent_refs).toHaveLength(2);
});