/**
 * Pins `toSubmitDTO` against a payload that the live simulator gateway actually
 * accepted (HTTP 200, run_id returned).
 *
 * The suite's own `--manifest` payload is the reference: same tenant, same
 * identity source, same document. If the builder ever stops producing this shape
 * -- a renamed field, a manifest dropped to "", an identity moved per-agent --
 * this fails here instead of at a live submit.
 */
import { createNewAgentRef, toSubmitDTO, type ComputeDraft } from '@/domain/agentswarm/draft';

const MANIFEST_DOC =
  "schema_version: 1\n" +
  'manifest:\n' +
  '  identity:\n' +
  "    - type: SOUL.md\n" +
  "      content: 'You are a meticulous greeter. Always answer politely.'\n" +
  '  mcp: []\n' +
  '  resources: []\n' +
  '  skills: []\n' +
  '  cli_tools: []\n' +
  'script:\n' +
  "  body: 'echo manifest-provisioned'";

const TENANT = 'teamclaw';
/**
 * Illustrative only. A real identity Bot id is minted per deployment and is
 * never a constant -- the picker reads it from `GET /openapi/v1/bots` at
 * runtime. What this fixture proves is the *shape*: the source rides on the
 * submit, one per run, and is not resolved client-side.
 */
const IDENTITY = '<deploy-minted-bot-id>';

/**
 * The run payload is now always a roster of definition refs, so the accepted
 * shape is the ref list plus the run-level fields. The agent *body* (manifest,
 * instructions) travels in the definition, not here.
 */
const ACCEPTED_REF_BODY = {
  tenant: TENANT,
  identity_bot_id: IDENTITY,
  goal: 'Say hello world',
  options: { model_name: 'DeepSeek-V4.1-Flash' },
  agent_refs: [{ definition_id: 'def-manifest-greeter', alias: 'manifest_greeter' }],
};

const draft = (): ComputeDraft => ({
  goal: 'Say hello world',
  identityBotId: IDENTITY,
  options: { model_name: 'DeepSeek-V4.1-Flash' },
  refs: [
    {
      ...createNewAgentRef({
        key: 'k',
        name: 'manifest_greeter',
        role: 'hello world producer',
        kind: 'bot',
        instructions: 'Respond with exactly the single word: hello world',
        config_manifest: MANIFEST_DOC,
      }),
      alias: 'manifest_greeter',
    },
  ],
});

const RESOLVED = { k: 'def-manifest-greeter' };

test('the builder reproduces every field the live gateway accepted', () => {
  expect(toSubmitDTO(draft(), TENANT, RESOLVED)).toEqual(ACCEPTED_REF_BODY);
});

test('the run payload carries no inline agent body', () => {
  // The manifest lives in the definition; sending it on the submit would mean
  // the two copies could disagree about what the agent actually is.
  const dto = toSubmitDTO(draft(), TENANT, RESOLVED);
  expect(dto).not.toHaveProperty('agents');
  expect(JSON.stringify(dto)).not.toContain('meticulous greeter');
});

test('the definition body keeps the manifest byte for byte', () => {
  const entry = draft().refs[0];
  expect(entry.body.config_manifest).toBe(MANIFEST_DOC);
  expect(entry.body.config_manifest).toContain('meticulous greeter');
  expect(entry.body.config_manifest).toContain('echo manifest-provisioned');
});

test('the run-level identity rides on the submit, not on a ref', () => {
  const dto = toSubmitDTO(draft(), TENANT, RESOLVED);
  expect(dto.identity_bot_id).toBe(IDENTITY);
  // `identity_bot_id` on an AgentDefinitionRefDTO would be a 422 (extra="forbid").
  expect(dto.agent_refs?.[0]).not.toHaveProperty('identity_bot_id');
});

test('the payload carries no draft-only key', () => {
  const dto = toSubmitDTO(draft(), TENANT, RESOLVED);
  expect(dto.agent_refs?.[0]).not.toHaveProperty('key');
  expect(Object.keys(dto).sort()).toEqual(
    ['agent_refs', 'goal', 'identity_bot_id', 'options', 'tenant'].sort(),
  );
});

test('a blank alias is omitted so the definition name applies', () => {
  const bare = draft();
  bare.refs = bare.refs.map((ref) => ({ ...ref, alias: '' }));
  expect(toSubmitDTO(bare, TENANT, RESOLVED).agent_refs?.[0]).toEqual({
    definition_id: 'def-manifest-greeter',
  });
});
