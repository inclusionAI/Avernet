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

export const MANIFEST_TEMPLATE = `# Full grammar: ${MANIFEST_DOCS_URL}
schema_version: 1
manifest:
  identity:
    - type: SOUL.md
      content: |-
        You are a general-purpose assistant agent.
        Do what the task asks, report results honestly, and do not invent
        information you were not given.

  # Optional categories, uncomment and fill in (see the doc above): mcp / skills
`;
