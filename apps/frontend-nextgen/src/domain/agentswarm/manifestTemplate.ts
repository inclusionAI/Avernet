/**
 * A fill-in manifest, shown when a user opts into writing their own.
 *
 * Deliberately small. The full grammar has six categories, named sources, two
 * source protocols and per-engine file allowlists — all of it documented in the
 * backend's `manifest-schema.zh-CN.md`, none of it useful to someone who just
 * wants to attach an MCP server or a skill. A template carrying every shape
 * buries the two fields the user came to edit, so only `mcp` and `skills` are
 * shown as examples and the rest is one line in 配置说明.
 *
 * Every example is commented out: an empty list is a *declaration that the set
 * is empty* and clears that category's area, so a template full of `[]` would
 * quietly instruct users to empty areas they had not considered.
 */

/** The authoritative grammar, linked from the template and the limits panel. */
export const MANIFEST_DOCS_URL =
  'https://github.com/inclusionAI/Avernet/blob/dev/apps/backend/docs/bot-config-manifest/manifest-schema.zh-CN.md';

export const MANIFEST_TEMPLATE = `# Full reference: ${MANIFEST_DOCS_URL}
schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      content: |-
        You are a general-purpose assistant agent.
        Do what the task asks, report results honestly, and do not invent
        information you were not given.

  # MCP servers. server_code must already be registered in the MCP Center
  # and available to your tenant; this file cannot register a new one.
  # mcp:
  #   - server_code: github
  #     config:
  #       endpoint_env: PRE            # PROD | PRE

  # Skills. An OSS source must pin a digest.
  # skills:
  #   - name: order-lookup
  #     source:
  #       protocol: oss
  #       bucket: artifacts
  #       key: tools/order-lookup-1.4.0.zip
  #       auth: oss-artifacts
  #     digest: "sha256:<content digest>"
  #     unpack: zip
`;

/** Field-level limits the gateway enforces, so a user is not surprised at submit. */
export const MANIFEST_LIMITS: readonly string[] = [
  "identity's type must belong to the current engine's identity file allowlist",
  'MEMORY.md is refused: it is engine runtime memory and cannot be declared',
  "mcp's server_code must already exist in the MCP Center and be available to the tenant",
  'skills and cli_tools must carry a digest when sourced from OSS',
  'resources paths are relative to the bot workspace; directories must end with /',
  'engine_config is not appliable yet and is rejected on write',
  'script is not supported on the teclaw engine',
  'An empty list (e.g. skills: []) clears that category; omitting the key leaves it untouched',
];