// RPC handlers for `commands.*` methods.
//
// Same shape as `src/skills/handlers.ts`: pure async functions of
// (store, params) → CommandsResult<T>; the frame dispatcher flattens the
// result into a WS response frame.
//
// Methods:
//   - commands.list — `{ commands: SlashCommand[] }`. Accepts optional
//                     `cwd` so project-level `<cwd>/.claude/commands/` is
//                     included; if omitted only built-in + user + plugin
//                     commands are returned.
//   - commands.get  — `{ command: SlashCommand }` or NOT_FOUND. Accepts
//                     either `id` (`compact`) or `name` (`/compact`).
//
// Discovery is read-only — there are no install / uninstall handlers in
// this phase. To add a custom command, drop a `.md` file under
// `~/.claude/commands/` (or your project's `.claude/commands/`); the next
// `commands.list` call will pick it up.

import type { CommandsStore, SlashCommand } from './store.js';

export type CommandsResult<T> =
  | { ok: true; payload: T }
  | { ok: false; error: { code: string; message: string } };

export type CommandsHandler = (
  store: CommandsStore,
  params: unknown,
) => Promise<CommandsResult<unknown>>;

function asObject(value: unknown): Record<string, unknown> {
  return (value && typeof value === 'object' && !Array.isArray(value))
    ? value as Record<string, unknown>
    : {};
}

function asString(v: unknown): string | undefined {
  return typeof v === 'string' && v.length > 0 ? v : undefined;
}

function ok<T>(payload: T): CommandsResult<T> { return { ok: true, payload }; }
function err(code: string, message: string): CommandsResult<never> {
  return { ok: false, error: { code, message } };
}

export async function handleList(
  store: CommandsStore,
  params: unknown,
): Promise<CommandsResult<{ commands: SlashCommand[] }>> {
  const cwd = asString(asObject(params).cwd);
  return ok({ commands: store.list(cwd) });
}

export async function handleGet(
  store: CommandsStore,
  params: unknown,
): Promise<CommandsResult<{ command: SlashCommand }>> {
  const p = asObject(params);
  const idOrName = asString(p.id) ?? asString(p.name);
  if (!idOrName) return err('INVALID_PARAMS', 'id or name is required');
  const cwd = asString(p.cwd);
  const command = store.get(idOrName, cwd);
  if (!command) return err('NOT_FOUND', `command not found: ${idOrName}`);
  return ok({ command });
}

export const COMMANDS_METHODS: Record<string, CommandsHandler> = {
  'commands.list': handleList,
  'commands.get': handleGet,
};
