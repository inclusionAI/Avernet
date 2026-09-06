import type { NodeExecution, WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import NodeOutputViewer from './NodeOutputViewer'
import {
  buildSource,
  extractTemplateFields,
  hasPlaceholder,
  resolveTemplateClient,
} from './template-render'

interface RenderedNodeHeroProps {
  node: NodeExecution
  workflowSpec?: WorkflowSpec
  nodes: NodeExecution[]
}

const TRUNCATE_THRESHOLD = 10240

/** Caveat shown on client-rendered (non-authoritative) hero views. */
function ClientRenderCaveat() {
  return (
    <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-amber-700 text-xs">
      前端解析 · 引擎未保存渲染后入参,可能与实际执行略有差异;未解析的占位符保留原样。
    </div>
  )
}

/** Resolve a value that may be a templated string; non-strings are JSON-encoded. */
function renderArgValue(value: unknown, source: Record<string, unknown>): string {
  if (typeof value === 'string') return resolveTemplateClient(value, source)
  return JSON.stringify(value)
}

/**
 * The primary "what this node actually ran" view, shown at the top of the
 * node-detail panel. Consolidates the rendered subject into one card per node
 * (instead of a flat stack of per-field viewers):
 *
 * - Agent family → engine `resolved_prompt` (authoritative), or a client-rendered
 *   `prompt`/`message` fallback with a caveat.
 * - cli-script → one "渲染后命令 / 脚本" card: the FULL reconstructed command line
 *   (`command` + resolved `args`, mimicking the engine's argv join) as the primary
 *   block, with resolved `env` as compact key=value sub-rows.
 * - baas-call / mcp-call / other → one "渲染后参数" card with the executor fields.
 *
 * Returns null when there is nothing to render (no spec, no command/prompt/fields,
 * no resolved_prompt).
 */
export default function RenderedNodeHero({ node, workflowSpec, nodes }: RenderedNodeHeroProps) {
  const type = node.executor_type

  // ── Agent family: prefer the engine-rendered resolved_prompt ──
  if (type === 'embedded-agent' || type === 'subagent' || type === 'collaboration' || type === 'human') {
    if (node.resolved_prompt) {
      return (
        <NodeOutputViewer
          nodeId={node.node_id}
          label="渲染后提示词"
          data={node.resolved_prompt}
          isTruncated={node.resolved_prompt.length > TRUNCATE_THRESHOLD}
        />
      )
    }
    // Fallback: no persisted resolved_prompt — client-render the spec prompt/message.
    const specNode = workflowSpec?.nodes.find((n) => n.id === node.node_id)
    const executor = (specNode?.executor as Record<string, unknown>) ?? {}
    const tmpl =
      typeof executor.prompt === 'string' && executor.prompt.trim() ? executor.prompt
      : typeof executor.message === 'string' && executor.message.trim() ? executor.message
      : null
    if (!tmpl) return null
    const rendered = resolveTemplateClient(tmpl, buildSource(node, nodes))
    return (
      <div className="space-y-1.5">
        {hasPlaceholder(tmpl) && <ClientRenderCaveat />}
        <NodeOutputViewer
          nodeId={node.node_id}
          label="渲染后提示词"
          data={rendered}
          isTruncated={rendered.length > TRUNCATE_THRESHOLD}
        />
      </div>
    )
  }

  // ── spec-based executors: require the workflow spec node ──
  const specNode = workflowSpec?.nodes.find((n) => n.id === node.node_id)
  if (!specNode) return null
  const executor = (specNode.executor as Record<string, unknown>) ?? {}
  const source = buildSource(node, nodes)

  // cli-script: reconstruct the FULL command line (command + args) the engine runs.
  // ClawMind builds argv = [bin, ...commandParts, ...resolvedArgs] (executors/cli-script.ts),
  // so the complete script is command + resolved args joined — not command and args apart.
  if (type === 'cli-script') {
    const command = typeof executor.command === 'string' ? executor.command : ''
    const resolvedCmd = command ? resolveTemplateClient(command, source) : ''

    let argsRepr = ''
    const args = executor.args
    if (Array.isArray(args)) {
      argsRepr = args.map((v) => renderArgValue(v, source)).join(' ')
    } else if (args != null && typeof args === 'object') {
      // Object args → `--key value` flags (engine also exposes ARG_<KEY> env, omitted here).
      argsRepr = Object.entries(args as Record<string, unknown>)
        .map(([k, v]) => `--${k} ${renderArgValue(v, source)}`)
        .join(' ')
    }

    const fullLine = argsRepr ? `${resolvedCmd} ${argsRepr}` : resolvedCmd

    const envEntries: { k: string; v: string }[] = []
    const env = executor.env
    if (env != null && typeof env === 'object' && !Array.isArray(env)) {
      for (const [k, v] of Object.entries(env as Record<string, unknown>)) {
        envEntries.push({ k, v: renderArgValue(v, source) })
      }
    }

    if (!fullLine && envEntries.length === 0) return null

    // Serialize the full command line + env into one text block so it goes through
    // NodeOutputViewer (foldable, copy, truncation) — consistent with Input/Output.
    const lines: string[] = []
    if (fullLine) lines.push(fullLine)
    if (envEntries.length > 0) {
      lines.push('', 'env:')
      for (const e of envEntries) lines.push(`  ${e.k}=${e.v}`)
    }
    const data = lines.join('\n')

    const showCaveat =
      hasPlaceholder(command) ||
      (args != null && hasPlaceholder(JSON.stringify(args))) ||
      envEntries.some((e) => hasPlaceholder(e.v))

    return (
      <div className="space-y-1.5">
        {showCaveat && <ClientRenderCaveat />}
        <NodeOutputViewer
          nodeId={node.node_id}
          label="渲染后命令 / 脚本"
          data={data}
          isTruncated={data.length > TRUNCATE_THRESHOLD}
        />
      </div>
    )
  }

  // baas-call / mcp-call / other: one consolidated params block via NodeOutputViewer.
  const fields = extractTemplateFields(executor)
  if (fields.length === 0) return null
  const hasTemplate = fields.some((f) => hasPlaceholder(f.raw))
  const data = fields
    .map((f) => `${f.label}:\n${resolveTemplateClient(f.raw, source)}`)
    .join('\n\n')

  return (
    <div className="space-y-1.5">
      {hasTemplate && <ClientRenderCaveat />}
      <NodeOutputViewer
        nodeId={node.node_id}
        label="渲染后参数"
        data={data}
        isTruncated={data.length > TRUNCATE_THRESHOLD}
      />
    </div>
  )
}