import type { WorkflowNode } from '@avernet/clawweb-shared/web/types'

export type NodeMeta = Readonly<{
  type: string
  label: string
  description: string
  group: 'common' | 'advanced'
  icon: string
}>

export const NODE_CATALOG: readonly NodeMeta[] = [
  { type: 'embedded-agent', label: 'AI 处理', description: '用任务指令处理内容，可选技能', group: 'common', icon: '✦' },
  { type: 'cli-script', label: '脚本执行', description: '执行命令或脚本，独立配置参数', group: 'common', icon: '⌘' },
  { type: 'human', label: '人工输入', description: '等待用户输入、确认或修订', group: 'common', icon: '◎' },
  { type: 'approval', label: '审批', description: '配置审批内容、人员与通过规则', group: 'common', icon: '✓' },
  { type: 'mcp-call', label: '工具调用', description: '调用已配置的 MCP 服务与工具', group: 'common', icon: '⚒' },
  { type: 'done', label: '完成', description: '标记此节点完成，不取消其他分支', group: 'common', icon: '◉' },
  { type: 'subagent', label: '子代理', description: '由指定技能执行独立任务', group: 'advanced', icon: '◇' },
  { type: 'loop-group', label: '循环', description: '保留循环配置，在完整 JSON 中编辑', group: 'advanced', icon: '↻' },
  { type: 'action', label: '外部动作', description: '配置已有动作及其参数', group: 'advanced', icon: '↗' },
  { type: 'collaboration', label: '协作', description: '配置参与方、协作消息与路由', group: 'advanced', icon: '⋈' },
  { type: 'bcs-route', label: '协作路由', description: '向指定协作目标发送消息', group: 'advanced', icon: '⇢' },
  { type: 'baas-call', label: 'Bot 调用', description: '配置 Bot 调用，连接信息由使用者提供', group: 'advanced', icon: '▣' },
  { type: 'subworkflow', label: '子工作流', description: '调用已有工作流', group: 'advanced', icon: '⊞' },
]

export function getNodeMeta(type: string): NodeMeta {
  return NODE_CATALOG.find(item => item.type === type) ?? {
    type, label: type, description: '兼容配置模式：保留真实类型与全部原始字段', group: 'advanced', icon: '◇',
  }
}

export function createEditorNode(type: string, position?: { x: number; y: number }): WorkflowNode & { _x?: number; _y?: number } {
  // No sample commands, accounts, skills, endpoints or implicit engine defaults.
  const fields: Record<string, Record<string, unknown>> = {
    'embedded-agent': { prompt: '' },
    'cli-script': { command: '' },
    human: { prompt: '' },
    approval: { skillName: '', message: '' },
    'mcp-call': { server: '', tool: '' },
    subagent: { skillName: '', prompt: '' },
    action: { action: '' },
    collaboration: { message: '' },
    'bcs-route': { target: '', message: '' },
    'baas-call': { message: '' },
    subworkflow: { workflowId: '' },
  }
  const random = typeof globalThis.crypto.randomUUID === 'function'
    ? globalThis.crypto.randomUUID()
    : Array.from(globalThis.crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('')
  return {
    id: `node-${random}`,
    title: NODE_CATALOG.some(item => item.type === type) ? getNodeMeta(type).label : `扩展节点（${type}）`,
    phase: 'P1',
    executor: { type, ...(fields[type] ?? {}) },
    ...(position ? { _x: position.x, _y: position.y } : {}),
  }
}
