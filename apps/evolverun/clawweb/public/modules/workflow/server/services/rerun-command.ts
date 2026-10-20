/**
 * Resolve the slash command that re-triggers a run.
 *
 * Three sources, in priority order:
 *
 * 1. The command recorded on the run itself (`input_json.command`). A
 *    facade-triggered run stores the exact invocation it received, e.g.
 *    `{"command":"/social-appeal 2088242692000193"}`, so it replays verbatim.
 * 2. The workflow's facade binding plus the run's message, as
 *    `/<facade> <message>`. Cron, dashboard and gateway runs record no command,
 *    only a message and params.
 * 3. `/workflow run <workflowId>`, when the run records no message. This is the
 *    Bot's own documented invocation and the only form that runs a workflow
 *    without a task description.
 *
 * The distinction in (2) and (3) matters: a bare slash command is a help query
 * to the Bot, so it replies with the command's usage text and starts nothing.
 */

/** facade_bindings stores the bare name ("ids-log-self-audit"); the Bot expects a slash command. */
export function toSlashCommand(bound: string | null | undefined): string {
  const trimmed = bound?.trim() ?? "";
  if (!trimmed) return "";
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
}

function parseObject(json: string | null): Record<string, unknown> | null {
  if (json == null || json === "") return null;
  try {
    const value: unknown = JSON.parse(json);
    return value !== null && typeof value === "object" && !Array.isArray(value)
      ? value as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
}

/**
 * Build the message body for a re-run.
 *
 * `lookupBoundCommand` is invoked only for a run that records a message but no
 * command, so the other two branches never pay for a binding query.
 */
export async function resolveRerunCommand(input: {
  inputJson: string | null;
  workflowId: string;
  lookupBoundCommand: () => Promise<string | null | undefined>;
}): Promise<string | null> {
  const parsed = parseObject(input.inputJson);
  const recorded = typeof parsed?.command === "string" ? parsed.command.trim() : "";
  // A recorded command is the exact original invocation and already carries its
  // own arguments, so it is replayed as-is.
  if (recorded) return recorded;

  // Without a workflow id there is nothing left to address, so the caller can
  // report an actionable error instead of sending a malformed command.
  const workflowId = input.workflowId?.trim() ?? "";
  if (!workflowId) return null;

  const message = typeof parsed?.message === "string" ? parsed.message.trim() : "";
  if (message) {
    const bound = toSlashCommand(await input.lookupBoundCommand());
    // Without a binding the workflow is still addressable by id, and the
    // documented form accepts a trailing task description.
    return bound ? `${bound} ${message}` : `/workflow run ${workflowId} ${message}`;
  }

  return `/workflow run ${workflowId}`;
}