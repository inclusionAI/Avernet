import type { NodeExecution } from '@/components/BotWorkshop/TaskEscort/types';
import { STATUS_LABEL, STATUS_TONE } from '@/components/BotWorkshop/TaskEscort/utils';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { cn } from '@/utils/cn';
import { X } from 'lucide-react';
import React from 'react';
import { NodeOutputViewer } from './NodeOutputViewer';

interface NodeDetailPanelProps {
  node: NodeExecution;
  onClose: () => void;
}

const CONTEXT_LABELS: Record<string, string> = {
  triggerRule: '触发规则',
  phase: '阶段',
  retry: '重试配置',
  failureReason: '失败原因',
  willRetry: '将重试',
  retryAttempt: '重试次数',
  reason: '原因',
};

const FAILURE_REASON_LABELS: Record<string, string> = {
  'executor-failed': '执行器失败',
  'output-contract-failed': '输出契约校验失败',
  trigger_rule_not_satisfied: '触发规则未满足',
};

function TokenUsageDisplay({ json }: { json: string }) {
  try {
    const parsed = JSON.parse(json);
    const input = typeof parsed.input === 'number' ? parsed.input : 0;
    const output = typeof parsed.output === 'number' ? parsed.output : 0;
    return (
      <div className="rounded-md bg-muted px-3 py-2 text-[10px] text-muted-foreground">
        <span className="font-medium">Tokens：</span>
        <span className="text-primary">{input.toLocaleString()} 输入</span> /{' '}
        <span className="text-success">{output.toLocaleString()} 输出</span> /{' '}
        <span className="font-medium">{(input + output).toLocaleString()} 总计</span>
      </div>
    );
  } catch {
    return null;
  }
}

function SystemContextPanel({ json }: { json: string }) {
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(json);
  } catch {
    return (
      <div>
        <h4 className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          系统上下文 (原始)
        </h4>
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-muted p-2 font-mono text-xs text-foreground">
          {json}
        </pre>
      </div>
    );
  }

  function renderEntries(entries: Record<string, unknown>, depth = 0): React.ReactNode {
    return (
      <div className={depth > 0 ? 'ml-3 border-l-2 border-border pl-2' : 'space-y-1'}>
        {Object.entries(entries)
          .filter(([, v]) => v !== undefined)
          .map(([key, value]) => {
            const label = CONTEXT_LABELS[key] ?? key;
            if (value === null || value === undefined) return null;
            if (typeof value === 'object' && !Array.isArray(value)) {
              return (
                <div key={key}>
                  <span className="text-[10px] font-medium text-muted-foreground">{label}:</span>
                  {renderEntries(value as Record<string, unknown>, depth + 1)}
                </div>
              );
            }
            let display: React.ReactNode = String(value);
            if (key === 'failureReason' && typeof value === 'string') {
              display = FAILURE_REASON_LABELS[value] ?? value;
            }
            if (typeof value === 'boolean') {
              display = value ? '✓ 是' : '✗ 否';
            }
            return (
              <div key={key} className="flex items-baseline gap-2 text-[10px]">
                <span className="shrink-0 font-medium text-muted-foreground">{label}:</span>
                <span className={cn('font-mono', key === 'failureReason' ? 'text-destructive' : 'text-foreground')}>
                  {display}
                </span>
              </div>
            );
          })}
      </div>
    );
  }

  return (
    <div>
      <h4 className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">系统上下文</h4>
      <div className="rounded-md border border-border bg-muted p-2">{renderEntries(data)}</div>
    </div>
  );
}

export function NodeDetailPanel({ node, onClose }: NodeDetailPanelProps) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-xs font-medium">节点：{node.node_title || node.node_id}</h3>
          <div className="mt-0.5 truncate text-[10px] text-muted-foreground">
            {node.node_id} · {node.executor_type}
            {node.phase && ` · ${node.phase}`}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={STATUS_TONE[node.status] || 'neutral'}>{STATUS_LABEL[node.status] || node.status}</Badge>
          <Button variant="ghost" size="icon" className="h-6 w-6 shrink-0" onClick={onClose}>
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      <div className="space-y-3">
        <NodeOutputViewer label="Input" data={node.input_json} />
        <NodeOutputViewer label="Output" data={node.output_json} />
        {node.error_text && (
          <div>
            <h4 className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-destructive">错误信息</h4>
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md border border-destructive/20 bg-destructive/10 p-2 font-mono text-xs leading-relaxed text-destructive">
              {node.error_text}
            </pre>
          </div>
        )}
        {node.system_context_json && <SystemContextPanel json={node.system_context_json} />}
        {node.token_usage_json && <TokenUsageDisplay json={node.token_usage_json} />}
      </div>
    </div>
  );
}
