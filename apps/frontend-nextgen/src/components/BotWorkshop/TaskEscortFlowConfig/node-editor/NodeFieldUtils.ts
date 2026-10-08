import type { TaskEscortWorkflowNode } from '@/components/BotWorkshop/TaskEscort/types';

// ── 执行器字段定义 ──

export type FieldDef = {
  key: string;
  label: string;
  type: 'text' | 'textarea' | 'number' | 'json' | 'select';
  options?: string[];
  placeholder?: string;
  description?: string;
};

export const EXECUTOR_TYPES = [
  'embedded-agent',
  'action',
  'human',
  'loop-group',
  'collaboration',
  'done',
  'subagent',
  'bcs-route',
  'baas-call',
  'mcp-call',
  'cli-script',
  'subworkflow',
  'approval',
];

export const EXECUTOR_FIELDS: Record<string, FieldDef[]> = {
  'embedded-agent': [
    { key: 'executor.skillName', label: 'Skill Name', type: 'text', placeholder: 'general-agent' },
    { key: 'executor.prompt', label: 'Prompt', type: 'textarea' },
    { key: 'executor.model', label: 'Model', type: 'text', placeholder: 'gpt-4o' },
    { key: 'executor.outputMode', label: 'Output Mode', type: 'select', options: ['text', 'json'] },
    { key: 'executor.timeoutSeconds', label: 'Timeout (s)', type: 'number', placeholder: '60' },
  ],
  action: [
    { key: 'executor.tool', label: 'Tool', type: 'text' },
    { key: 'executor.action', label: 'Action', type: 'text', placeholder: 'http-request' },
    { key: 'executor.input', label: 'Input (JSON)', type: 'json' },
    { key: 'executor.args', label: 'Args (JSON)', type: 'json' },
  ],
  human: [
    { key: 'executor.prompt', label: 'Prompt', type: 'textarea' },
    { key: 'executor.waitKind', label: 'Wait Kind', type: 'text', placeholder: 'gate' },
  ],
  'loop-group': [
    { key: 'executor.maxIterations', label: 'Max Iterations', type: 'number', placeholder: '10' },
    { key: 'executor.iterationVar', label: 'Iteration Variable', type: 'text', placeholder: 'itemIndex' },
    { key: 'executor.loopOver', label: 'Loop Over (expr)', type: 'text' },
    { key: 'executor.itemName', label: 'Item Name', type: 'text', placeholder: 'item' },
  ],
  collaboration: [
    { key: 'executor.taskKind', label: 'Task Kind', type: 'text', placeholder: 'analysis' },
    { key: 'executor.skillName', label: 'Skill Name', type: 'text', placeholder: 'data-analyst' },
    { key: 'executor.message', label: 'Message', type: 'textarea' },
    { key: 'executor.routeDisplayName', label: 'Route Display Name', type: 'text' },
    { key: 'executor.timeoutSeconds', label: 'Timeout (s)', type: 'number', placeholder: '120' },
  ],
  done: [{ key: 'executor.message', label: 'Message', type: 'textarea' }],
  subagent: [
    { key: 'executor.skillName', label: 'Skill Name', type: 'text' },
    { key: 'executor.prompt', label: 'Prompt', type: 'textarea' },
    { key: 'executor.timeoutSeconds', label: 'Timeout (s)', type: 'number', placeholder: '60' },
  ],
  'bcs-route': [
    { key: 'executor.target', label: 'Target', type: 'text' },
    { key: 'executor.message', label: 'Message', type: 'textarea' },
  ],
  'baas-call': [
    { key: 'executor.mode', label: '调用模式', type: 'select', options: ['run', 'message'] },
    {
      key: 'executor.botId',
      label: 'Bot ID',
      type: 'text',
      placeholder: 'real_bot_id:staff_no',
      description: 'mode=message 时必填',
    },
    { key: 'executor.message', label: '消息', type: 'textarea' },
    { key: 'executor.apiKeyRef', label: 'API Key 环境变量', type: 'text', placeholder: 'BAAS_API_KEY' },
    { key: 'executor.baseUrl', label: 'Base URL', type: 'text', placeholder: 'https://your-baas.example.com' },
    { key: 'executor.timeoutMs', label: '超时(ms)', type: 'number', placeholder: '120000' },
    { key: 'executor.pollIntervalMs', label: '轮询间隔(ms)', type: 'number', placeholder: '3000' },
    { key: 'executor.outputMode', label: '输出模式', type: 'select', options: ['text', 'json'] },
  ],
  'mcp-call': [
    { key: 'executor.server', label: 'MCP Server', type: 'text', placeholder: 'mcp.ant.agentix.xxx' },
    { key: 'executor.tool', label: 'Tool Name', type: 'text', placeholder: 'risk_evaluation_toolkit' },
    { key: 'executor.args', label: 'Args (JSON)', type: 'json', placeholder: '{"key": "{{value}}"}' },
    { key: 'executor.outputMode', label: 'Output Mode', type: 'select', options: ['text', 'json'] },
    { key: 'executor.timeoutMs', label: 'Timeout (ms)', type: 'number', placeholder: '30000' },
  ],
  'cli-script': [
    { key: 'executor.command', label: 'Command', type: 'text' },
    { key: 'executor.args', label: 'Args (JSON)', type: 'json' },
    { key: 'executor.outputMode', label: 'Output Mode', type: 'select', options: ['text', 'json'] },
    { key: 'executor.timeoutMs', label: 'Timeout (ms)', type: 'number', placeholder: '30000' },
  ],
  subworkflow: [
    { key: 'executor.workflowId', label: 'Workflow ID', type: 'text' },
    { key: 'executor.packId', label: 'Pack ID', type: 'text', description: 'Optional' },
  ],
  approval: [
    { key: 'executor.skillName', label: 'Skill Name', type: 'text' },
    { key: 'executor.approvalType', label: 'Approval Type', type: 'text' },
    { key: 'executor.message', label: 'Message', type: 'textarea' },
    { key: 'executor.timeoutSeconds', label: 'Timeout (s)', type: 'number', placeholder: '300' },
  ],
};

/** 读取 executor 子字段值，返回字符串表示 */
export function getExecutorFieldValue(key: string, node: TaskEscortWorkflowNode): string {
  const parts = key.split('.');
  if (parts.length === 2 && parts[0] === 'executor') {
    const executor = typeof node.executor === 'object' && node.executor ? node.executor : {};
    const val = executor[parts[1]];
    if (typeof val === 'string') return val;
    if (val !== undefined && val !== null) return JSON.stringify(val, null, 2);
  }
  return '';
}

/** 更新 executor 子字段值 */
export function handleExecutorFieldChange(
  key: string,
  value: string,
  node: TaskEscortWorkflowNode,
  onChange: (updates: Partial<TaskEscortWorkflowNode>) => void,
): void {
  const parts = key.split('.');
  if (parts.length !== 2 || parts[0] !== 'executor') return;
  const field = parts[1];
  const executor = typeof node.executor === 'object' && node.executor ? { ...node.executor } : { type: 'done' };

  let parsedValue: unknown = value;
  if (field === 'input' || field === 'args' || value.startsWith('{') || value.startsWith('[')) {
    try {
      parsedValue = JSON.parse(value);
    } catch {
      /* keep as string */
    }
  }
  if (field === 'maxIterations' || field === 'timeoutSeconds' || field === 'timeoutMs' || field === 'pollIntervalMs') {
    parsedValue = value === '' ? undefined : Number(value);
  }
  onChange({ executor: { ...executor, [field]: parsedValue } });
}

/** 判断节点是否有高级配置 */
export function hasAdvancedConfig(node: TaskEscortWorkflowNode): boolean {
  return !!(
    node.retry ||
    node.output ||
    node.config ||
    (Array.isArray(node.onResult) && node.onResult.length > 0) ||
    Object.keys(node.input ?? {}).length > 0 ||
    Object.keys(node.output ?? {}).length > 0 ||
    Object.keys(node.config ?? {}).length > 0
  );
}

/** 判断节点是否有 PostAction 配置 */
export function hasPostActions(node: TaskEscortWorkflowNode): boolean {
  return (
    (Array.isArray(node.onSuccess) && node.onSuccess.length > 0) ||
    (Array.isArray(node.onFailure) && node.onFailure.length > 0)
  );
}
