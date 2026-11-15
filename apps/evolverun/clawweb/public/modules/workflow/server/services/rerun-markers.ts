import { normalizeFlowRunMarkers } from "./flow-run-markers.js";

type RunMarkers = { env?: string | null; tag?: string | null };
type CommandToken = { raw: string; value: string };

/** ClawMind command syntax: only double quotes group text; backslashes escape inside quotes. */
function commandTokens(command: string): CommandToken[] {
  const tokens: CommandToken[] = [];
  let start = 0;
  let value = "";
  let inQuote = false;
  let escaped = false;
  for (let i = 0; i <= command.length; i++) {
    const char = command[i];
    if (char === undefined) {
      if (inQuote) throw new Error("Unclosed quote in recorded workflow command");
      if (i > start) tokens.push({ raw: command.slice(start, i), value });
      break;
    }
    if (escaped) { value += char; escaped = false; continue; }
    if (char === "\\" && inQuote) { escaped = true; continue; }
    if (char === '"') { inQuote = !inQuote; continue; }
    if (!inQuote && /\s/.test(char)) {
      if (i > start) tokens.push({ raw: command.slice(start, i), value });
      start = i + 1;
      value = "";
    } else value += char;
  }
  return tokens;
}

/** This is workflow-command quoting, not shell quoting or JSON string escaping. */
function quoteCommandValue(value: string): string {
  return `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

/** Replace only instance markers. Business arguments and the release --tag remain untouched. */
export function withRerunMarkers(command: string, input: RunMarkers): string {
  const markers = normalizeFlowRunMarkers(input);
  const tokens = commandTokens(command);
  const kept: string[] = [];
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    if (token.value === "--run-env" || token.value === "--run-tag") {
      const next = tokens[i + 1];
      if (!next || next.value.startsWith("--")) throw new Error("Missing recorded run marker value");
      i++;
    } else if (!token.value.startsWith("--run-env=") && !token.value.startsWith("--run-tag=")) {
      kept.push(token.raw);
    }
  }
  // Default runs need no new flags, so their reruns remain compatible with older engines.
  if ((markers.env ?? "prod") === "prod" && (markers.tag ?? "") === "") {
    return kept.length === tokens.length ? command : kept.join(" ");
  }
  return `${kept.join(" ")} --run-env=${quoteCommandValue(markers.env ?? "prod")} --run-tag=${quoteCommandValue(markers.tag ?? "")}`;
}
