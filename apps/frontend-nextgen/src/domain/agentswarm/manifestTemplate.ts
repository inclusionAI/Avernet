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
  'identity 的 type 必须属于当前引擎的身份文件白名单',
  'MEMORY.md 会被拒绝：它是引擎运行期记忆，不可由清单声明',
  'mcp 的 server_code 必须是 MCP Center 已登记且租户可用的服务',
  'skills 与 cli_tools 走 oss 来源时必须提供 digest',
  'resources 的 path 相对 bot workspace，目录须以 / 结尾',
  'engine_config 当前不可用，写入会被拒绝',
  'script 在 teclaw 引擎上不支持',
  '声明为空列表（如 skills: []）会清空该类目；不写这个键则不碰',
];