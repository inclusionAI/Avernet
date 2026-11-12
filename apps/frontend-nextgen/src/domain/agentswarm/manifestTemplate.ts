/**
 * A fill-in manifest, shown when a user opts into writing their own.
 *
 * Every category is commented out at its default rather than left as `[]`,
 * because an empty list is a *declaration that the set is empty* — it removes
 * everything in that category's area. A user reading a template full of `[]`
 * would reasonably conclude they were "empty defaults" and submit them.
 *
 * The grammar citations are to `apps/backend/docs/bot-config-manifest/
 * manifest-schema.zh-CN.md` in the Avernet repository; that document is the
 * authority and this template only carries the shape.
 */
export const MANIFEST_TEMPLATE = `# Bot 配置清单（bot_config_manifest）
#
# 三段都可以省略；省略即不声明该类目。注意：本清单是「收敛式」的——
# 声明为空列表（如 skills: []）表示「这个类目应当为空」，会移除该区域
# 内的全部条目；不写这个键则不会碰它。
#
# 可选类目：mcp / resources / skills / identity / cli_tools
# engine_config 当前不可用（写入时会被拒绝，见 CMP-2858）
# script 在 teclaw 引擎上不受支持

schema_version: 1

# ── 命名源（可选）────────────────────────────────────────────
# 一处声明、多处引用。不写也可以：每个条目可以各自内联 source。
# sources:
#   content:
#     protocol: git
#     url: https://code.example-corp.com/team/content.git
#     ref: v1.2.0
#     auth: corp-git-content

manifest:
  # ── 人设文件 ───────────────────────────────────────────────
  # type 必须是当前引擎允许的身份文件（通用：SOUL.md / RULES.md / AGENTS.md /
  # IDENTITY.md 等；claude_code 引擎只允许 CLAUDE.md）。
  # MEMORY.md 会被直接拒绝——它是引擎运行期记忆。
  identity:
    - type: SOUL.md
      content: |-
        你是一个通用型助手 Agent。
        按任务目标执行工作，如实汇报结果，不臆测未提供的信息。

  # ── MCP 服务 ───────────────────────────────────────────────
  # server_code 必须是 MCP Center 已登记、且当前租户有权使用的服务。
  # 这里不能注册新 MCP，也不能写任意启动命令。
  # config 只能覆盖 url / headers / endpoint_env / transport_protocol。
  # mcp:
  #   - server_code: github
  #     config:
  #       url: https://bot.example.com/mcp
  #       headers:
  #         X-Project: alpha
  #       endpoint_env: PRE            # PROD | PRE
  #       transport_protocol: STREAMABLE_HTTP   # SSE | STREAMABLE_HTTP

  # ── 资源文件 ───────────────────────────────────────────────
  # path 是相对 bot workspace 的落点，目录须以 / 结尾。
  # 来源可以内联 content，也可以引用 git / oss。
  # resources:
  #   - path: data/kb/
  #     source:
  #       protocol: git
  #       url: https://code.example-corp.com/team/content.git
  #       ref: v1.2.0
  #       subpath: kb/                 # 仓库内路径，缺省为仓库根
  #       auth: corp-git-content

  # ── 技能 ───────────────────────────────────────────────────
  # 当前支持 oss 上的 zip 包；oss + skills 必须钉住 digest。
  # skills:
  #   - name: order-lookup
  #     source:
  #       protocol: oss
  #       bucket: artifacts
  #       key: tools/order-lookup-1.4.0.zip
  #       auth: oss-artifacts
  #     digest: "sha256:<内容摘要>"
  #     unpack: zip

  # ── 命令行工具 ─────────────────────────────────────────────
  # 由平台托管安装；name 是命令名。
  # cli_tools:
  #   - name: mycli
  #     source:
  #       protocol: oss
  #       bucket: cdn
  #       key: mycli
  #       auth: oss-cred
  #     digest: "sha256:<内容摘要>"

# ── 启动脚本（可选）──────────────────────────────────────────
# teclaw 引擎会拒绝这一节。
# script:
#   body: |
#     #!/bin/bash
#     echo ready
`;

/** Field-level limits the gateway enforces, so a user is not surprised at submit. */
export const MANIFEST_LIMITS: readonly string[] = [
  'identity 的 type 必须属于当前引擎的身份文件白名单',
  'MEMORY.md 会被拒绝：它是引擎运行期记忆，不可由清单声明',
  'mcp 的 server_code 必须是 MCP Center 已登记且租户可用的服务',
  'skills 与 cli_tools 走 oss 来源时必须提供 digest',
  'engine_config 当前不可用，写入会被拒绝',
  'script 在 teclaw 引擎上不支持',
];