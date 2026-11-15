/**
 * Pins `toSubmitDTO` against a payload that the live simulator gateway actually
 * accepted (HTTP 200, run_id returned).
 *
 * The suite's own `--manifest` payload is the reference: same tenant, same
 * identity source, same document. If the builder ever stops producing this shape
 * -- a renamed field, a manifest dropped to "", an identity moved per-agent --
 * this fails here instead of at a live submit.
 */
import { createAgentDraft, toSubmitDTO, type ComputeDraft } from '@/domain/agentswarm/draft';

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

/** The exact body that returned 200 from POST /openapi/v1/agentswarm/runs. */
const ACCEPTED_BODY = {
  tenant: TENANT,
  identity_bot_id: IDENTITY,
  goal: 'Say hello world',
  options: { model_name: 'DeepSeek-V4.1-Flash' },
  agents: [
    {
      name: 'manifest_greeter',
      role: 'hello world producer',
      kind: 'bot',
      instructions: 'Respond with exactly the single word: hello world',
      config_manifest: MANIFEST_DOC,
    },
  ],
};

const draft = (): ComputeDraft => ({
  goal: 'Say hello world',
  identityBotId: IDENTITY,
  options: { model_name: 'DeepSeek-V4.1-Flash' },
  agents: [
    createAgentDraft({
      key: 'k',
      name: 'manifest_greeter',
      role: 'hello world producer',
      kind: 'bot',
      instructions: 'Respond with exactly the single word: hello world',
      config_manifest: MANIFEST_DOC,
    }),
  ],
});

test('the builder reproduces every field the live gateway accepted', () => {
  // The accepted body omitted engine/bot_type, so the gateway defaulted them.
  // The builder now sends them explicitly, which must not disturb anything else.
  const dto = toSubmitDTO(draft(), TENANT);
  expect(dto).toMatchObject({ ...ACCEPTED_BODY, agents: [expect.objectContaining(ACCEPTED_BODY.agents[0])] });
});

test('engine and bot_type are fixed and not user-chosen', () => {
  const dto = toSubmitDTO(draft(), TENANT);
  expect(dto.agents[0].engine).toBe('openclaw');
  expect(dto.agents[0].bot_type).toBe('personal');
});

test('the manifest survives the round trip byte for byte', () => {
  const dto = toSubmitDTO(draft(), TENANT);
  expect(dto.agents[0].config_manifest).toBe(MANIFEST_DOC);
  expect(dto.agents[0].config_manifest).toContain('meticulous greeter');
  expect(dto.agents[0].config_manifest).toContain('echo manifest-provisioned');
});

test('the run-level identity rides on the submit, not on the agent', () => {
  const dto = toSubmitDTO(draft(), TENANT);
  expect(dto.identity_bot_id).toBe(IDENTITY);
  // `identity_bot_id` on AgentSpecDTO would be a 422 (extra="forbid").
  expect(dto.agents[0]).not.toHaveProperty('identity_bot_id');
});

test('the payload carries no draft-only key', () => {
  const dto = toSubmitDTO(draft(), TENANT);
  expect(dto.agents[0]).not.toHaveProperty('key');
  expect(Object.keys(dto).sort()).toEqual(
    ['agents', 'goal', 'identity_bot_id', 'options', 'tenant'].sort(),
  );
});

test('the same draft without a manifest still submits, minus that field', () => {
  const bare = draft();
  bare.agents = bare.agents.map((agent) => ({ ...agent, config_manifest: '' }));
  const dto = toSubmitDTO(bare, TENANT);
  expect(dto.agents[0]).not.toHaveProperty('config_manifest');

  // Everything else is unchanged, so only the manifest decides the persona source.
  const withoutManifest = (agent: object) => {
    const copy = { ...(agent as Record<string, unknown>) };
    delete copy.config_manifest;
    return copy;
  };
  expect({ ...dto, agents: dto.agents.map(withoutManifest) }).toMatchObject({
    ...ACCEPTED_BODY,
    agents: ACCEPTED_BODY.agents.map(withoutManifest),
  });
});