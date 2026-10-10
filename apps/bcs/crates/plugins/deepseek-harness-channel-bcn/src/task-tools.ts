import { defineTool, type ToolDefinition } from '@deepseek-ai/dsh-tools';
import type { BcnBridge } from './bridge.js';

export const BCS_ASSIGN_TASK_TOOL_NAME = 'bcs_assign_task';
export const BCS_SEND_TASK_MESSAGE_TOOL_NAME = 'bcs_send_task_message';

export function createBcsAssignTaskTool(bridge: BcnBridge): ToolDefinition {
  return defineTool({
    name: BCS_ASSIGN_TASK_TOOL_NAME,
    description:
      'Dispatch a task to a sub bot in this task group. Returns immediately; ' +
      "the sub bot's response will arrive as a follow-up message. " +
      'You can dispatch multiple sub bots in parallel.',
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
      );
    },
  });
}

export function createBcsSendTaskMessageTool(bridge: BcnBridge): ToolDefinition {
  return defineTool({
    name: BCS_SEND_TASK_MESSAGE_TOOL_NAME,
    description:
      'Send a task-scoped message from this worker bot to the manager bot. ' +
      'Use this for progress updates, blockers, intermediate findings, or supplemental information.',
    parameters: {
      message: {
        type: 'string',
        required: true,
        description: 'The task-scoped message to send to the manager bot.',
      },
    },
    output: {
      schema: { type: 'json' },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(args, exec) {
      return bridge.sendTaskMessage(exec.agent, args.message, exec.signal);
    },
  });
}
