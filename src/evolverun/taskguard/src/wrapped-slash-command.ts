export type WrappedWorkflowSlashCommand =
  | {
      kind: "command";
      commandName: string;
      skillName: string;
      raw: string;
      displayCommand: string;
    }
  | { kind: "ambiguous"; message: string }
  | { kind: "none" };

const DEFAULT_ALLOWED_COMMANDS = ["workflow"];
const MESSAGE_MARKER = "[消息内容]";

function normalizeNewlines(text: string): string {
  return text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
}

function stripFencedCodeBlocks(text: string): string {
  return text.replace(/```[\s\S]*?```/g, "");
}

function scopedText(text: string): string {
  const normalized = normalizeNewlines(text);
  const markerIndex = normalized.lastIndexOf(MESSAGE_MARKER);
  if (markerIndex < 0) return normalized;
  return normalized.slice(markerIndex + MESSAGE_MARKER.length);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function buildWorkflowCommandMatcher(allowedCommands: string[]): RegExp | undefined {
  const commands = Array.from(new Set(
    allowedCommands
      .map((command) => command.trim().replace(/^\/+/, ""))
      .filter(Boolean),
  ));
  if (commands.length === 0) return undefined;
  const alternation = commands
    .sort((left, right) => right.length - left.length)
    .map(escapeRegExp)
    .join("|");
  // Match /command at the start of a line, optionally preceded by:
  //   - [xxx] bracket prefix (e.g. "[trade-risk-analysis-flow] /command ...")
  //   - BCS IM platform prefix "收到命令：" or "收到来令：" (Chinese colon or ASCII colon)
  // This avoids matching /command mentioned in natural language mid-sentence.
  return new RegExp(`^(?:\\[[^\\]]+\\]\\s*)?(?:收到[命令来令]+[：:]\\s*)?\\/(${alternation})(?:[ \\t]+([\\s\\S]*))?$`);
}

export function extractWrappedWorkflowSlashCommand(
  body: string,
  options: { allowedCommands?: string[] } = {},
): WrappedWorkflowSlashCommand {
  const matcher = buildWorkflowCommandMatcher(options.allowedCommands ?? DEFAULT_ALLOWED_COMMANDS);
  if (!matcher) return { kind: "none" };

  const scanText = stripFencedCodeBlocks(scopedText(body));
  const lines = scanText.split("\n");
  const candidates: Array<{
    lineIndex: number;
    commandName: string;
    args: string;
  }> = [];

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i].trim();
    if (!line) continue;
    const match = line.match(matcher);
    if (!match) continue;
    candidates.push({
      lineIndex: i,
      commandName: match[1],
      args: (match[2] ?? "").trim(),
    });
  }

  if (candidates.length === 0) return { kind: "none" };
  if (candidates.length > 1) {
    return {
      kind: "ambiguous",
      message: "一次只支持执行一个 workflow 命令，请拆成多条消息发送。",
    };
  }

  const candidate = candidates[0];
  const trailing = lines
    .slice(candidate.lineIndex + 1)
    .join("\n")
    .trim();
  const raw = [candidate.args, trailing].filter(Boolean).join("\n");

  return {
    kind: "command",
    commandName: candidate.commandName,
    skillName: candidate.commandName,
    raw,
    displayCommand: `/${candidate.commandName}${candidate.args ? ` ${candidate.args}` : ""}`,
  };
}
