import { defineTool, type ToolDefinition } from '@deepseek-ai/dsh-tools';
import type { BcnBridge } from './bridge.js';

export const BCS_ASSIGN_TASK_TOOL_NAME = 'bcs_assign_task';
export const BCS_TASK_COMPLETE_TOOL_NAME = 'bcs_task_complete';

export function createBcsAssignTaskTool(bridge: BcnBridge): ToolDefinition {
  return defineTool({
    name: BCS_ASSIGN_TASK_TOOL_NAME,
    description:
      'Dispatch a task to a sub bot in this task group. Returns immediately; ' +
      "the sub bot's response will arrive as a follow-up message. " +
      'You can dispatch multiple sub bots in parallel. Use task_ref for on-demand status/cancel. ' +
      'For a blocked result, send a follow-up to the same Worker and include needed context. ' +
      'unknown/not_found never proves the previous task stopped or is safe to resend.',
    parameters: {
      target_bot: {
        type: 'string',
        required: true,
        description:
          "Target bot name (for example 'DBA') or bot ID (for example 'bot_abc123'). " +
          "Use one or the other, not a combined 'name(id)' value.",
      },
      message: {
        type: 'string',
        required: true,
        description: 'The task description or instruction to send to the sub bot.',
      },
      timeout_s: { type: 'number', description: 'Optional positive runtime seconds; clamped to server policy.' },
      response_mode: {
        type: 'string',
        enum: ['after-last-tool-call', 'full'],
        description:
          "Use 'after-last-tool-call' for the final answer after tool calls (default), " +
          "or 'full' for the complete response text.",
      },
    },
    output: {
      schema: { type: 'json' },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(args, exec) {
      return bridge.assignTask(
        exec.agent,
        args.target_bot,
        args.message,
        args.response_mode,
        exec.signal,
        args.timeout_s,
      );
    },
  });
}

export function createBcsTaskCompleteTool(bridge: BcnBridge): ToolDefinition {
  return defineTool({
    name: BCS_TASK_COMPLETE_TOOL_NAME,
    description:
      "Signal that the task group's work is fully done. Only call this after receiving replies " +
      'from all sub bots and completing the final analysis. Provide a comprehensive summary of all results.',
    parameters: {
      summary: {
        type: 'string',
        required: true,
        description: "Final summary of the task group's work and results.",
      },
    },
    output: {
      schema: { type: 'json' },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(args, exec) {
      return bridge.completeTask(exec.agent, args.summary, exec.signal);
    },
  });
}


export function createBcsTaskStatusTool(bridge: BcnBridge): ToolDefinition {
  return defineTool({
    name: 'bcs_task_status',
    description: 'Query 1..20 task refs on demand, not by polling. unknown/not_found never proves stopped or safe to resend.',
    parameters: { task_refs: { type: 'array', items: { type: 'string' }, required: true } },
    output: { schema: { type: 'json' }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
    async execute(args, exec) { return bridge.taskControl(exec.agent, 'task.status', { task_refs: args.task_refs }, exec.signal); },
  });
}
export function createBcsCancelTaskTool(bridge: BcnBridge): ToolDefinition {
  return defineTool({
    name: 'bcs_cancel_task',
    description: 'Request cancellation and wait briefly for confirmed stop. unknown/not_found/cancel_failed is not stopped. Side effects are not rolled back.',
    parameters: { task_ref: { type: 'string', required: true }, reason: { type: 'string', required: true } },
    output: { schema: { type: 'json' }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
    async execute(args, exec) { return bridge.taskControl(exec.agent, 'task.cancel', args, exec.signal); },
  });
}
