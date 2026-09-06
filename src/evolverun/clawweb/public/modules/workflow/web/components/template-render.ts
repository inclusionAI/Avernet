/**
 * Shared template-rendering helpers for node-detail UI.
 *
 * Client-side approximation of ClawMind's `resolveTemplate`
 * (ClawMind/src/actions/template.ts). Covers the common placeholder paths used
 * in specs; exotic paths (iteration.* in batch runs, workflowData.*, custom
 * formatters) are left as the original `{{...}}` so divergence is visible.
 *
 * Used by `RenderedNodeHero` (the consolidated subject at the top of node detail)
 * and reusable for any other client-side rendered view of an executor config.
 */
import type { NodeExecution } from '@avernet/clawweb-shared/web/types'

// Mirrors the path-token charset of ClawMind's template regex
// (actions/template.ts `isTemplate`): word chars, '.', '@', '-', and [digits].
// The `g` flag is needed for `replace()` (.Replace all); for a presence test use
// TEMPLATE_TEST_RE below — a global regex's `.test()` is stateful and would
// alternate true/false across calls in a loop.
export const TEMPLATE_RE =
  /\{\{([\w@.-]+(?:\[[\d]+\])?(?:\.[\w@.-]+(?:\[[\d]+\])?)*)\}\}/g

// Non-global copy for `hasPlaceholder`. Never stateful.
const TEMPLATE_TEST_RE =
  /\{\{([\w@.-]+(?:\[[\d]+\])?(?:\.[\w@.-]+(?:\[[\d]+\])?)*)\}\}/

export function lookup(path: string, source: Record<string, unknown>): unknown {
  const parts = path.replace(/\[(\d+)\]/g, '.$1').split('.')
  let current: unknown = source
  for (const part of parts) {
    if (current == null || typeof current !== 'object') return undefined
    current = (current as Record<string, unknown>)[part]
  }
  return current
}

export function formatValue(resolved: unknown): string {
  if (resolved == null) return ''
  if (typeof resolved === 'object') return JSON.stringify(resolved)
  return String(resolved)
}

/** Resolve placeholders in a string. Unresolved paths keep `{{...}}` intact. */
export function resolveTemplateClient(
  template: string,
  source: Record<string, unknown>,
): string {
  return template.replace(TEMPLATE_RE, (match, path: string) => {
    const resolved = lookup(path, source)
    if (resolved == null) return match // keep original placeholder when unresolved
    return formatValue(resolved)
  })
}

/** True if a string contains any `{{...}}` placeholder. */
export function hasPlaceholder(value: unknown): value is string {
  return typeof value === 'string' && TEMPLATE_TEST_RE.test(value)
}

export interface RenderedField {
  label: string
  raw: string
}

/** Extract user-authored templated fields from the executor for preview. */
export function extractTemplateFields(executor: Record<string, unknown>): RenderedField[] {
  const fields: RenderedField[] = []
  const type = String(executor.type ?? '')
  const push = (label: string, value: unknown) => {
    if (typeof value === 'string' && value.trim().length > 0) {
      fields.push({ label, raw: value })
    }
  }

  if (type === 'cli-script') {
    push('command', executor.command)
    const args = executor.args
    if (args != null && typeof args === 'object' && !Array.isArray(args)) {
      for (const [k, v] of Object.entries(args as Record<string, unknown>)) {
        push(`args.${k}`, v)
      }
    } else if (Array.isArray(args)) {
      push('args[]', JSON.stringify(args))
    }
    const env = executor.env
    if (env != null && typeof env === 'object' && !Array.isArray(env)) {
      for (const [k, v] of Object.entries(env as Record<string, unknown>)) {
        push(`env.${k}`, v)
      }
    }
    return fields
  }

  if (type === 'embedded-agent' || type === 'subagent' || type === 'collaboration' || type === 'human') {
    push('prompt', executor.prompt)
    push('message', executor.message)
    return fields
  }

  if (type === 'baas-call') {
    push('url', executor.url)
    push('baseUrl', executor.baseUrl)
    push('path', executor.path)
    push('body', executor.body)
    push('method', executor.method)
    return fields
  }

  if (type === 'mcp-call') {
    push('server', executor.server)
    push('tool', executor.tool)
    push('input', executor.input ? JSON.stringify(executor.input) : undefined)
    return fields
  }

  // Generic fallback: surface any string field that contains a placeholder.
  for (const [k, v] of Object.entries(executor)) {
    if (k === 'type') continue
    if (hasPlaceholder(v)) push(k, v)
  }
  return fields
}

/**
 * Build the resolution source (nodeOutput + params + input.params) from a node's
 * execution + its sibling executions. Prefers succeeded outputs.
 */
export function buildSource(
  node: NodeExecution,
  nodes: NodeExecution[],
): Record<string, unknown> {
  const nodeOutput: Record<string, unknown> = {}
  for (const n of nodes) {
    if (!n.output_json) continue
    const existing = nodeOutput[n.node_id]
    if (existing == null || n.status === 'succeeded') {
      try {
        nodeOutput[n.node_id] = JSON.parse(n.output_json)
      } catch {
        nodeOutput[n.node_id] = n.output_json
      }
    }
  }

  let params: unknown = undefined
  try {
    const inputObj = node.input_json ? JSON.parse(node.input_json) : null
    if (inputObj != null && typeof inputObj === 'object' && 'params' in (inputObj as Record<string, unknown>)) {
      params = (inputObj as Record<string, unknown>).params
    }
  } catch {
    // ignore — params stays undefined
  }

  return {
    nodeOutput,
    params: params ?? {},
    input: { params: params ?? {} },
  }
}