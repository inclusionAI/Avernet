import type { ActionHandler, ActionExecutionContext, ActionRegistry } from "./types.js";

function validateRequiredArgs(handler: ActionHandler, args: Record<string, unknown>): void {
  for (const key of handler.requiredArgs ?? []) {
    const value = args[key];
    if (value == null || value === "") {
      throw new Error(`Action ${handler.name} missing required arg: ${key}`);
    }
  }
}

export function createActionRegistry(): ActionRegistry {
  const handlers = new Map<string, ActionHandler>();

  return {
    register(handler) {
      if (handlers.has(handler.name)) {
        throw new Error(`Action already registered: ${handler.name}`);
      }
      handlers.set(handler.name, handler);
    },

    has(name) {
      return handlers.has(name);
    },

    names() {
      return [...handlers.keys()].sort();
    },

    async execute(name: string, args: Record<string, unknown>, context: ActionExecutionContext) {
      const handler = handlers.get(name);
      if (!handler) {
        throw new Error(`Unknown action: ${name}`);
      }
      validateRequiredArgs(handler, args);
      return handler.execute({ args, context });
    },
  };
}
