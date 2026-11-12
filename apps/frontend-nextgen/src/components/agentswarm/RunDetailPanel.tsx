import { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, RotateCw } from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Spin } from '@/components/ui/Spin';
import { notifyError, notifySuccess } from '@/components/ui/notify';
import { AgentswarmRequestError } from '@/services/agentswarm/http';
import { cancelRun } from '@/services/agentswarm/service';
import {
  isTerminalStatus,
  runStatusLabel,
  type AgentDetailDTO,
  type RunDetailDTO,
  type RunSummaryDTO,
} from '@/services/agentswarm/types';

function renderValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** Epoch milliseconds from the gateway; tolerate seconds from older payloads. */
function formatTime(value: number | null | undefined): string | null {
  if (!value) return null;
  const millis = value > 10_000_000_000 ? value : value * 1000;
  const date = new Date(millis);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString('zh-CN', { hour12: false });
}

function agentTone(status: string): 'success' | 'error' | 'primary' | 'neutral' {
  if (status === 'COMPLETED') return 'success';
  if (status === 'FAILED') return 'error';
  if (status === 'RUNNING') return 'primary';
  return 'neutral';
}

function AttemptLine({ attempt }: { attempt: NonNullable<AgentDetailDTO['attempt_ledger']>[number] }) {
  return (
    <span className="flex flex-wrap gap-x-2">
      <span className="text-muted-foreground">#{attempt.slice_retry_index}</span>
      <span>{attempt.executor_name || attempt.executor_id}</span>
      <span className="font-mono text-muted-foreground">{attempt.executor_kind}</span>
      {attempt.binding_state ? <span>{attempt.binding_state}</span> : null}
      {attempt.result_status ? <span>{attempt.result_status}</span> : null}
    </span>
  );
}

function AgentRow({ agent, defaultOpen }: { agent: AgentDetailDTO; defaultOpen: boolean }) {
  // Each agent owns its own disclosure, so opening one does not move the others.
  const [open, setOpen] = useState(defaultOpen);
  const messages = agent.messages ?? [];
  const attempts = agent.attempt_ledger ?? [];

  return (
    <div className="rounded-md border border-border bg-background">
      <Button
        type="button"
        variant="ghost"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className="flex h-auto w-full items-center justify-start gap-2 rounded-md p-2 text-left font-normal"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">{agent.name}</span>
        <Badge tone="outline">{agent.kind}</Badge>
        <Badge tone={agentTone(agent.status)}>{agent.status}</Badge>
      </Button>

      {open ? (
        <div className="flex flex-col gap-2 border-t border-border p-2">
          {agent.role ? <p className="text-[11px] text-muted-foreground">{agent.role}</p> : null}
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
            {agent.attempts > 1 ? <span>尝试 {agent.attempts} 次</span> : null}
            {agent.bot_state ? <span>Bot {agent.bot_state}</span> : null}
            {agent.bot_id ? <span className="font-mono">{agent.bot_id}</span> : null}
          </div>
          {agent.error ? <p className="text-xs text-destructive">{agent.error}</p> : null}
          {agent.output !== undefined && agent.output !== null ? (
            <div className="flex flex-col gap-1">
              <span className="text-[11px] font-medium text-foreground">输出</span>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-muted p-2 text-[11px]">
                {renderValue(agent.output)}
              </pre>
            </div>
          ) : null}
          {messages.length ? (
            <details className="text-xs">
              <summary className="cursor-pointer text-muted-foreground">消息记录（{messages.length}）</summary>
              <div className="mt-2 flex flex-col gap-1">
                {messages.map((message) => (
                  <div key={message.message_id} className="rounded bg-muted/60 p-2">
                    <span className="font-medium text-muted-foreground">{message.role}</span>
                    <pre className="mt-1 whitespace-pre-wrap break-words text-[11px]">
                      {renderValue(message.content)}
                    </pre>
                  </div>
                ))}
              </div>
            </details>
          ) : null}
          {attempts.length ? (
            <details className="text-xs">
              <summary className="cursor-pointer text-muted-foreground">执行尝试（{attempts.length}）</summary>
              <ul className="mt-2 flex flex-col gap-1">
                {attempts.map((attempt, index) => (
                  <li key={`${attempt.executor_id}-${index}`} className="rounded bg-muted/60 p-2 text-[11px]">
                    <AttemptLine attempt={attempt} />
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}


export interface RunDetailPanelProps {
  tenant: string | undefined;
  /** The run this panel is showing. */
  run: RunSummaryDTO;
  /** The run's goal, when known; the list response does not carry it. */
  goal?: string;
  detail: RunDetailDTO | null;
  loading: boolean;
  error: string | null;
  onReload: () => void;
  onCancelled: () => void;
}

/**
 * One run's detail, rendered directly under its row in the list.
 *
 * Disclosure is local to this component: the per-agent view collapses and
 * expands on demand, and a finished run starts expanded while an active one
 * starts collapsed so its progress stays visible.
 */
export function RunDetailPanel({
  tenant,
  run,
  goal,
  detail,
  loading,
  error,
  onReload,
  onCancelled,
}: RunDetailPanelProps) {
  const finished = isTerminalStatus(run.status);
  const [agentsOpen, setAgentsOpen] = useState(finished);
  const [cancelling, setCancelling] = useState(false);

  // A run that finishes while open should reveal its agents without another click.
  useEffect(() => {
    if (finished) setAgentsOpen(true);
  }, [finished]);

  const handleCancel = async () => {
    if (!tenant) return;
    setCancelling(true);
    try {
      const result = await cancelRun(tenant, run.run_id);
      notifySuccess(`取消请求已受理，网关状态：${result.status}`);
      onCancelled();
    } catch (reason) {
      if (reason instanceof AgentswarmRequestError && reason.kind === 'not_found') {
        notifyError('该运行已不存在，可能已被清理');
      } else {
        notifyError(reason instanceof Error ? reason.message : '取消请求失败');
      }
    } finally {
      setCancelling(false);
    }
  };

  return (
    <div className="flex flex-col gap-2 rounded-md bg-muted/40 p-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        <Badge tone={finished ? (run.status === 'COMPLETED' ? 'success' : 'error') : 'primary'}>
          {runStatusLabel(run.status)}
        </Badge>
        {run.started_at ? <span>开始 {formatTime(run.started_at)}</span> : null}
        {run.ended_at ? <span>结束 {formatTime(run.ended_at)}</span> : null}
        {run.termination_reason ? <span>原因 {run.termination_reason}</span> : null}
        <div className="ml-auto flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={onReload} disabled={loading}>
            <RotateCw className="mr-1 h-3.5 w-3.5" />
            刷新
          </Button>
          {!finished ? (
            <Button variant="outline" size="sm" onClick={handleCancel} disabled={cancelling}>
              {cancelling ? '正在取消…' : '取消运行'}
            </Button>
          ) : null}
        </div>
      </div>

      {/* The full goal, clamped: it can be thousands of characters, and the
          详情区 should stay scannable. Expanded on demand rather than truncated
          silently. */}
      {goal?.trim() ? (
        <details className="group">
          <summary className="cursor-pointer list-none text-[11px] text-muted-foreground hover:text-foreground">
            <span className="line-clamp-2 break-words group-open:line-clamp-none">{goal.trim()}</span>
            <span className="mt-0.5 block text-[11px] text-primary group-open:hidden">展开目标</span>
            <span className="mt-0.5 hidden text-[11px] text-primary group-open:block">收起目标</span>
          </summary>
        </details>
      ) : null}

      {run.error ? <p className="text-xs text-destructive">{run.error}</p> : null}

      {finished && run.final_answer !== null && run.final_answer !== undefined ? (
        <div className="flex flex-col gap-1">
          <span className="text-[11px] font-medium text-foreground">最终结果</span>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-muted p-2 text-[11px]">
            {renderValue(run.final_answer)}
          </pre>
        </div>
      ) : null}

      <div className="flex items-center justify-between">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setAgentsOpen((current) => !current)}
          aria-expanded={agentsOpen}
          className="h-auto gap-1 px-1 py-0 text-[11px] font-medium"
        >
          {agentsOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          执行 Agent{detail?.agents?.length ? `（${detail.agents.length}）` : ''}
        </Button>
      </div>

      {agentsOpen ? (
        loading ? (
          <Spin tip="正在读取 Agent 详情…" />
        ) : error ? (
          <p className="text-xs text-destructive">{error}</p>
        ) : detail?.agents?.length ? (
          <div className="flex flex-col gap-2">
            {detail.agents.map((agent) => (
              <AgentRow key={agent.agent_id} agent={agent} defaultOpen />
            ))}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">暂无 Agent 详情。</p>
        )
      ) : null}
    </div>
  );
}