import type { CSSProperties } from 'react'
import type { WorkflowNode } from '@avernet/clawweb-shared/web/types'

export const NODE_DRAG_TYPE = 'application/clawflow-node-type'
export const GENERAL_SOURCE_HANDLE = 'source'
const BRANCH_HANDLE_PREFIX = 'branch:'
export const branchHandleId = (branchId: string) => `${BRANCH_HANDLE_PREFIX}${encodeURIComponent(branchId)}`
export function branchIdFromHandle(handle: string | null | undefined): string | undefined {
  if (!handle?.startsWith(BRANCH_HANDLE_PREFIX)) return undefined
  try { return decodeURIComponent(handle.slice(BRANCH_HANDLE_PREFIX.length)) } catch { return undefined }
}

export function nodeSummary(node: WorkflowNode): string {
  const exec: Record<string, unknown> = node.executor ?? {}
  if (exec.type === 'mcp-call') {
    return [exec.server, exec.tool].filter((value) => typeof value === 'string' && value.trim()).join(' / ')
  }
  for (const key of ['command', 'prompt', 'message', 'content', 'instructions', 'skillName', 'target', 'workflowId', 'botId', 'action']) {
    const value = exec[key]
    if (typeof value === 'string' && value.trim()) return value.replace(/\s+/g, ' ')
  }
  return ''
}

// @theme inline does not emit --color-* variables in the public host. Resolve
// semantic tokens through Tailwind, then expose them to React Flow's SVG/controls.
export const canvasThemeClasses = '[--canvas-primary:--theme(--color-primary)] [--canvas-foreground:--theme(--color-foreground)] [--canvas-card:--theme(--color-card)] [--canvas-muted:--theme(--color-muted)] [--canvas-muted-foreground:--theme(--color-muted-foreground)] [--canvas-border:--theme(--color-border)]'

export const canvasTheme = {
  '--xy-background-color': 'var(--canvas-muted)',
  '--xy-background-pattern-dots-color': 'var(--canvas-border)',
  '--xy-edge-stroke': 'var(--canvas-muted-foreground)',
  '--xy-edge-stroke-selected': 'var(--canvas-primary)',
  '--xy-connectionline-stroke': 'var(--canvas-primary)',
  '--xy-edge-label-background-color': 'var(--canvas-card)',
  '--xy-edge-label-color': 'var(--canvas-foreground)',
  '--xy-controls-button-background-color': 'var(--canvas-card)',
  '--xy-controls-button-background-color-hover': 'var(--canvas-muted)',
  '--xy-controls-button-color': 'var(--canvas-foreground)',
  '--xy-controls-button-color-hover': 'var(--canvas-primary)',
  '--xy-controls-button-border-color': 'var(--canvas-border)',
  '--xy-minimap-background-color': 'var(--canvas-card)',
  '--xy-minimap-mask-background-color': 'color-mix(in srgb, var(--canvas-muted) 70%, transparent)',
  '--xy-minimap-node-background-color': 'var(--canvas-muted-foreground)',
  '--xy-minimap-node-stroke-color': 'var(--canvas-border)',
  '--xy-selection-background-color': 'color-mix(in srgb, var(--canvas-primary) 10%, transparent)',
  '--xy-selection-border': '1px solid var(--canvas-primary)',
} as CSSProperties
