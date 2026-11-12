import { containsSecret, redactEvidenceValue } from "@avernet/clawweb-shared/server/services/redaction";

type ObjectValue = Record<string, unknown>;
type ComparableUrl = { resource: string; credentials: string };
type Comparison = { call_line: number; call_index: number } & (
  | { status: "match" | "mismatch"; source_line: number }
  | { status: "unavailable" }
);

function object(value: unknown): ObjectValue | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value as ObjectValue : undefined;
}

function comparableUrl(text: string): ComparableUrl | undefined {
  if (!/^https?:\/\//i.test(text) || /[\s"'<>\\$\x60()[\]{}]|\.\.\.|…/u.test(text) || !URL.canParse(text)) return;
  const url = new URL(text);
  if (url.username || url.password || url.hash || /(?:^|&)[^=&]*%/.test(url.search.slice(1))) return;
  const resource: [string, string][] = [];
  const credentials: [string, string][] = [];
  for (const [name, value] of url.searchParams) {
    if (containsSecret(`?${name}=placeholder`)) {
      if (!value || /[\[\]{}$*]|\.\.\.|…/u.test(value) || credentials.some(([key]) => key === name)) return;
      credentials.push([name, value]);
    } else {
      resource.push([name, value]);
    }
  }
  if (!credentials.length) return;
  // Sort by key only: the order of repeated resource parameters can be significant.
  const byKey = ([a]: [string, string], [b]: [string, string]) => a < b ? -1 : a > b ? 1 : 0;
  return { resource: JSON.stringify([url.origin, url.pathname, resource.sort(byKey)]),
    credentials: JSON.stringify(credentials.sort(byKey)) };
}

function sourceUrls(content: unknown): ComparableUrl[] {
  const text = typeof content === "string" ? [content] : Array.isArray(content)
    ? content.flatMap(part => part?.type === "text" && typeof part.text === "string" ? [part.text] : []) : [];
  return text.flatMap(value => [...value.matchAll(/\]\((https?:\/\/[^\s"'<>\\)\x60]+)\)|(https?:\/\/[^\s"'<>\\\x60]+)/gi)]
    .flatMap(match => comparableUrl(match[1] ?? match[2]) ?? []));
}

function argumentUrls(value: unknown, depth = 0): ComparableUrl[] {
  if (depth > 8) return [];
  if (typeof value === "string") return [comparableUrl(value)].filter(url => url !== undefined);
  if (Array.isArray(value)) return value.flatMap(item => argumentUrls(item, depth + 1));
  return Object.entries(object(value) ?? {}).flatMap(([key, item]) => {
    if (key !== "command" || typeof item !== "string") return argumentUrls(item, depth + 1);
    // Extract literal argument words without evaluating command syntax or expanding variables.
    return [...item.matchAll(/(?:^|\s)((?:'[^']*'|"(?:\\.|[^"\\])*"|\\[\s\S]|[^\s"'\\])+)/g)].flatMap(([, word]) => {
      if (word.startsWith("'") || word.startsWith('"')) {
        return word.endsWith(word[0]) ? comparableUrl(word.slice(1, -1)) ?? [] : [];
      }
      return /[;&|*?]/.test(word.replace("?", "")) ? [] : comparableUrl(word) ?? [];
    });
  });
}

function uniqueUrls(values: ComparableUrl[]): ComparableUrl[] {
  return [...new Map(values.map(url => [JSON.stringify([url.resource, url.credentials]), url])).values()];
}

function compareArguments(evidence: ObjectValue): Comparison[] {
  const lines = new Map<number, ObjectValue>();
  for (const page of [evidence.failure_context, evidence.messages]) {
    if (!Array.isArray(page)) continue;
    for (const item of page) {
      const record = object(item?.record);
      if (record && Number.isSafeInteger(item.line_no) && item.line_no > 0) lines.set(item.line_no, record);
    }
  }
  const results: Comparison[] = [];
  let sources: ComparableUrl[] = [], sourceLine = 0, previousLine = 0;
  for (const [line, record] of [...lines].sort(([a], [b]) => a - b)) {
    if (line !== previousLine + 1) sources = [];
    previousLine = line;
    const message = object(record.message);
    const content = message?.content;
    if (message?.role === "user") {
      // Claude Code encodes tool returns as user-role records as well.
      if (Array.isArray(content) && content.length && content.every(part => part?.type === "tool_result")) continue;
      sources = uniqueUrls(sourceUrls(content));
      sourceLine = line;
    }
    if (message?.role !== "assistant" || !Array.isArray(content)) continue;
    for (const [index, part] of content.entries()) {
      if (part?.type !== "toolCall" && part?.type !== "tool_use") continue;
      const args = uniqueUrls(argumentUrls(part.type === "toolCall" ? part.arguments : part.input));
      if (!args.length) continue;
      const position = { call_line: line, call_index: index };
      const [arg] = args;
      const matchingSources = args.length === 1 ? sources.filter(source => source.resource === arg.resource) : [];
      if (matchingSources.length === 1) {
        const [source] = matchingSources;
        results.push({ ...position, source_line: sourceLine, status: source.credentials === arg.credentials ? "match" : "mismatch" });
      } else {
        results.push({ ...position, status: "unavailable" });
      }
      if (results.length === 20) return results;
    }
  }
  return results;
}

/** Derive only positions and equality before credentials are removed from the receipt. */
export function redactSessionReadEvidence(value: ObjectValue): ObjectValue {
  const { url_argument_comparisons: _untrusted, ...evidence } = value;
  const comparisons = compareArguments(evidence);
  return redactEvidenceValue({
    ...evidence, ...(comparisons.length ? { url_argument_comparisons: comparisons } : {}),
  }) as ObjectValue;
}
