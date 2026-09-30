import { quoteCommandArgument, renderCommand } from "./command.js";

export type DiagnoseSessionSource = { mode?: string; session_ids?: unknown };

/** Matches the Diagnose runtime's exact identifier guard; never trims or parses intent. */
export function normalizeSessionIds(value: unknown, mode: unknown = "local", diagnoseEnabled = true): string[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) throw new Error("sessionIds/session_ids 必须是精确 Session ID 数组");
  for (const id of value) {
    if (typeof id !== "string" || !id || [...id].length > 512
      || !/^[\p{L}\p{N}_.:-]+$/u.test(id) || id.startsWith(".") || id.includes("..")
      || /\.jsonl?$/i.test(id)) {
      throw new Error("sessionIds/session_ids 含非法 ID；只允许精确 sessionId 或完整 sessionKey，不接受路径、空白或表达式");
    }
  }
  if (value.length && !diagnoseEnabled) throw new Error("未启用 Diagnose，不能指定非空 sessionIds/session_ids");
  if (value.length && mode !== "local") throw new Error("非空 sessionIds/session_ids 当前仅支持 local，不支持 service_export");
  return [...new Set<string>(value)];
}

/** Token spans preserve quoted intent verbatim while locating only CLI option tokens. */
function commandTokens(command: string): Array<{ start: number; end: number; value: string }> {
  const tokens: Array<{ start: number; end: number; value: string }> = [];
  let index = 0;
  while (index < command.length) {
    if (/\s/.test(command[index]!)) { index++; continue; }
    const start = index;
    let quote = "";
    let value = "";
    while (index < command.length) {
      const char = command[index]!;
      if (!quote && /\s/.test(char)) break;
      if (char === "\\" && quote !== "'") {
        if (++index >= command.length) throw new Error("Session 范围命令包含不完整转义");
        value += command[index++];
      } else if ((char === "'" || char === '"') && (!quote || quote === char)) {
        quote = quote ? "" : char;
        index++;
      } else { value += char; index++; }
    }
    if (quote) throw new Error("Session 范围命令包含未闭合引号");
    tokens.push({ start, end: index, value });
  }
  return tokens;
}

/** Frozen config is authoritative on both template and historical-command retries. */
export function withFrozenSessionIds(command: string, source?: DiagnoseSessionSource): string {
  const ids = normalizeSessionIds(source?.session_ids, source?.mode ?? "local");
  if (!ids.length) return command; // Do not rewrite historical unscoped commands.
  const tokens = commandTokens(command);
  const removed: Array<{ start: number; end: number }> = [];
  for (let index = 0; index < tokens.length; index++) {
    const part = tokens[index]!;
    const option = part.value.split("=", 1)[0]!;
    // argparse accepts unique option prefixes; they must not broaden the frozen set.
    if (!option.startsWith("--") || option.length <= 2 || !"--session-id".startsWith(option)) continue;
    let end = part.end;
    if (!part.value.includes("=")) {
      const argument = tokens[++index];
      if (!argument || argument.value.startsWith("--")) throw new Error("Session 范围命令缺少 --session-id 的值");
      end = argument.end;
    }
    removed.push({ start: part.start, end });
  }
  let scoped = command;
  for (const span of removed.reverse()) scoped = scoped.slice(0, span.start) + scoped.slice(span.end);
  const args = ids.map((id) => `--session-id${id.startsWith("-") ? "=" : " "}${quoteCommandArgument(id)}`).join(" ");
  return renderCommand(`${scoped.trimEnd()} ${args}`, {}, []);
}
