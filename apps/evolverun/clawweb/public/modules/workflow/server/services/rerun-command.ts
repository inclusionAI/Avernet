/**
 * Resolve the slash command that re-triggers a run.
 *
 * A facade-triggered run records the command it received in input_json, e.g.
 * {"command":"/social-appeal 2088242692000193"}, so it can be replayed verbatim.
 *
 * A cron-, dashboard- or gateway-triggered run records no command at all — its
 * input_json only carries params — so the workflow's facade binding supplies the
 * command instead. Without that lookup every scheduled workflow was unrerunnable.
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
 * Returns null when neither the run nor its binding yields a command, so the
 * caller can report an actionable error instead of sending an empty trigger.
 *
 * `lookupBoundCommand` is invoked only when the run records no command, so a
 * facade-triggered run never pays for a binding query.
 *
 * A binding supplies only the workflow name. Sending it bare makes the Bot
 * answer with the command's help text instead of starting a run, so the bound
 * form always carries a task description.
 */
export async function resolveRerunCommand(input: {
  inputJson: string | null;
  lookupBoundCommand: () => Promise<string | null | undefined>;
  /** Task description used when the binding supplies the command but the run recorded no message. */
  fallbackMessage: string;
}): Promise<string | null> {
  const parsed = parseObject(input.inputJson);
  const recorded = typeof parsed?.command === "string" ? parsed.command.trim() : "";
  const message = typeof parsed?.message === "string" ? parsed.message.trim() : "";

  // A recorded command is the exact original invocation and already carries its
  // own arguments, so it is replayed as-is.
  if (recorded) return message ? `${recorded} ${message}` : recorded;

  const bound = toSlashCommand(await input.lookupBoundCommand());
  if (!bound) return null;
  return `${bound} ${message || input.fallbackMessage}`;
}
