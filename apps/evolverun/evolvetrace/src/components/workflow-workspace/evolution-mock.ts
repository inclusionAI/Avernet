/**
 * 工作流进化 tab 的演示数据。
 * 后端经验库（lessons/remedies/suggestions）接口尚未落地，先 mock。
 * 所有工作流共用同一份演示数据，仅用于展示信息架构。
 */

export type WeakLink = {
  rank: number
  nodeId: string
  nodeName: string
  signature: string
  failureMode: string
  impactRuns: number
  failureRate: string
  hasRemedy: boolean
  suggestedKind: string
}

export type Remedy = {
  id: string
  signature: string
  executorType: string
  failureMode: string
  kind: 'kb_hint' | 'prompt_patch' | 'arg_template_fix' | 'node_patch' | 'alert'
  status: 'draft' | 'verified' | 'published' | 'retired'
  confidence: number
  hits: number
  rescued: number
  scope: string
  createdAt: string
  spec: string
}

export type Suggestion = {
  id: string
  weakNode: string
  signature: string
  failureMode: string
  kind: string
  impactRuns: number
  evidenceRuns: string[]
  description: string
}

export const MOCK_WEAK_LINKS: WeakLink[] = [
  {
    rank: 1,
    nodeId: 'risk-decision',
    nodeName: '风险决策推理',
    signature: 'output-contract · embedded-agent · risk-decision',
    failureMode: 'output-contract',
    impactRuns: 12,
    failureRate: '75.5%',
    hasRemedy: false,
    suggestedKind: 'prompt_patch',
  },
  {
    rank: 2,
    nodeId: 'tvm-per-ticket-loop',
    nodeName: '逐票处理循环',
    signature: 'timeout · cli-script · tvm-process-single',
    failureMode: 'timeout',
    impactRuns: 8,
    failureRate: '31.2%',
    hasRemedy: true,
    suggestedKind: 'node_patch',
  },
  {
    rank: 3,
    nodeId: 'fetch-data',
    nodeName: '威胁数据拉取',
    signature: 'arg-type-mismatch · mcp-call · fetch_data',
    failureMode: 'arg-type-mismatch',
    impactRuns: 42,
    failureRate: '18.4%',
    hasRemedy: true,
    suggestedKind: 'arg_template_fix',
  },
]

export const MOCK_REMEDIES: Remedy[] = [
  {
    id: 'R-142',
    signature: 'arg-type-mismatch · mcp-call · fetch_data',
    executorType: 'mcp-call',
    failureMode: 'arg-type-mismatch',
    kind: 'arg_template_fix',
    status: 'verified',
    confidence: 0.86,
    hits: 14,
    rescued: 12,
    scope: '全局',
    createdAt: '2026-08-10',
    spec: '{"domain_id": "{{int(domain_id)}}"}',
  },
  {
    id: 'R-151',
    signature: 'timeout · cli-script · tvm-process-single',
    executorType: 'cli-script',
    failureMode: 'timeout',
    kind: 'node_patch',
    status: 'published',
    confidence: 0.91,
    hits: 23,
    rescued: 21,
    scope: '当前工作流',
    createdAt: '2026-08-12',
    spec: '前置分块节点 + 超时调至 600s',
  },
  {
    id: 'R-203',
    signature: 'output-contract · embedded-agent · risk-decision',
    executorType: 'embedded-agent',
    failureMode: 'output-contract',
    kind: 'prompt_patch',
    status: 'draft',
    confidence: 0,
    hits: 0,
    rescued: 0,
    scope: '当前工作流',
    createdAt: '2026-08-18',
    spec: 'prompt 末尾追加 JSON schema 示例与必填字段约束',
  },
]

export const MOCK_SUGGESTIONS: Suggestion[] = [
  {
    id: 'S-1',
    weakNode: '风险决策推理',
    signature: 'output-contract · embedded-agent · risk-decision',
    failureMode: 'output-contract',
    kind: 'prompt_patch',
    impactRuns: 12,
    evidenceRuns: ['b2c3d4e5…7777', 'd4e5f6a7…9999'],
    description: '在风险决策节点 prompt 中新增 JSON schema 约束，要求输出必须包含 action/risk_level/reasoning。',
  },
  {
    id: 'S-2',
    weakNode: '逐票处理循环',
    signature: 'timeout · cli-script · tvm-process-single',
    failureMode: 'timeout',
    kind: 'node_patch',
    impactRuns: 8,
    evidenceRuns: ['a1b2c3d4…6666', 'c3d4e5f6…8888'],
    description: '在 TVM 处理前增加分块节点，将大工单拆分为小批次，避免单次超时。',
  },
]
