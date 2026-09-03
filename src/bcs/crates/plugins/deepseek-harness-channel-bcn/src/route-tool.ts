import { defineTool, type ToolDefinition } from '@deepseek-ai/dsh-tools';
import type { BcnBridge } from './bridge.js';

export const BCS_ROUTE_TOOL_NAME = 'bcs_route';

export function createBcsRouteTool(bridge: BcnBridge): ToolDefinition {
  return defineTool({
    name: BCS_ROUTE_TOOL_NAME,
    description:
      'Specify which bot or bots in the current BCN group should respond next. ' +
      'The routing intent is attached to the current final reply; do not duplicate it with @mentions.',
    parameters: {
      to: {
        type: 'array',
        required: true,
        description: 'One or more Bot display-name or bot_uuid selectors.',
        items: {
          type: 'object',
          additionalProperties: false,
          properties: {
            type: {
              type: 'string',
              enum: ['name', 'bot'],
              required: true,
              description: "Use 'name' for a Bot display name or 'bot' for a bot_uuid.",
            },
            value: { type: 'string', required: true },
          },
        },
      },
      reason: { type: 'string', required: true, description: 'Why this routing is needed.' },
    },
    output: {
      schema: { type: 'json' },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(args, exec) {
      return bridge.captureRoute(exec.agent, args.to, args.reason, exec.signal);
    },
  });
}
