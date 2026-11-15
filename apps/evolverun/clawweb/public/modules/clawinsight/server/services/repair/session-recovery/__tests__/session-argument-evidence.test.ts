import { expect, it } from "vitest";
import { compareTraceUrlArguments, redactSessionReadEvidence } from "../session-argument-evidence.js";

const original = new URL("https://example.invalid/report");
original.searchParams.set("signature", "Alpha");
original.searchParams.set("file", "report");
const reordered = new URL(original);
reordered.searchParams.sort();
const encoded = new URL(original);
encoded.search = encoded.search.replace("Alpha", "%41lpha");
const masked = new URL(original);
masked.searchParams.set("signature", "[REDACTED]");

function trace(...links: URL[]) {
  return { messages: [{ role: "user", content: links.map(link => `[Report](${link.href})`).join(" ") }] };
}

it.each([
  ["identical URL", original, "match"],
  ["reordered query parameters", reordered, "mismatch"],
  ["different percent-encoding", encoded, "mismatch"],
])("compares an OC tool argument with the literal source URL (%s)", (_, argument, status) => {
  expect(compareTraceUrlArguments(trace(original), { url: argument.href })).toEqual({ status, source_message_index: 0 });
});

it.each([
  ["Alpha", "match"],
  ["Alphb", "mismatch"],
])("compares literal curl input against the latest OC OpenTelemetry user message (%s)", (signature, status) => {
  const argument = new URL(original);
  argument.searchParams.set("signature", signature);
  const input = [
    { role: "user", parts: [{ type: "text", content: reordered.href }] },
    { role: "user", parts: [
      { type: "tool_result", content: reordered.href },
      { type: "text", content: original.href },
    ] },
    { role: "assistant", parts: [{ type: "text", content: reordered.href }] },
  ];
  expect(compareTraceUrlArguments(input, { command: `curl '${argument.href}'` }))
    .toEqual({ status, source_message_index: 1 });
});

it.each([
  ["a second command", (url: string) => `curl -sS -m 20 -w '\n%{http_code}\n' '${url}'; echo "curl_exit:$?"`],
  ["a pipeline and redirection", (url: string) => `curl --url="${url}" | head -c 1 >result`],
  ["a line break and unrelated URL", (url: string) => `echo '${original.href}'
curl '${url}'`],
  ["a conditional continuation", (url: string) => `/usr/bin/curl '${url}' && echo done`],
  ["a download utility", (url: string) => `wget -O report '${url}'`],
])("compares a literal download URL when the command includes %s", (_, command) => {
  const changed = new URL(original);
  changed.searchParams.set("signature", "Alphb");
  expect(compareTraceUrlArguments(trace(original), { command: command(original.href) }))
    .toEqual({ status: "match", source_message_index: 0 });
  expect(compareTraceUrlArguments(trace(original), { command: command(changed.href) }))
    .toEqual({ status: "mismatch", source_message_index: 0 });
});

it.each([
  ["a variable URL", 'curl "$LINK"'],
  ["an expanded argument next to a literal", `curl '${original.href}' "$ANOTHER_URL"`],
  ["a substitution inside the argument", 'curl "$(get_link)"'],
  ["multiple download calls", `curl '${original.href}'; curl "$LINK"`],
  ["a quoted shell script", `bash -c "curl '${original.href}'"`],
  ["a non-download command", `echo '${original.href}'`],
  ["a malformed command", `curl '${original.href}'; echo "`],
])("keeps the comparison unavailable for %s", (_, command) => {
  expect(compareTraceUrlArguments(trace(original), { command })).toEqual({ status: "unavailable" });
});

it("compares the literal signature in a signed object-storage URL", () => {
  const signed = new URL("https://example.invalid/report");
  signed.searchParams.set("OSSAccessKeyId", "fixture-key");
  signed.searchParams.set("Expires", "1893456000");
  signed.searchParams.set("Signature", "fixture+signature=");
  const changed = new URL(signed);
  changed.searchParams.set("Signature", "fixture+different=");
  expect(compareTraceUrlArguments(trace(signed), { command: `curl '${changed.href}'; echo "$?"` }))
    .toEqual({ status: "mismatch", source_message_index: 0 });
});

it("keeps distinct source links and tool arguments ambiguous even if they normalize to the same URL", () => {
  expect(compareTraceUrlArguments(trace(original, encoded), { url: original.href })).toEqual({ status: "unavailable" });
  expect(compareTraceUrlArguments(trace(original), { urls: [original.href, reordered.href] })).toEqual({ status: "unavailable" });
});

it("leaves redacted trace inputs unavailable", () => {
  expect(compareTraceUrlArguments(trace(masked), { url: original.href })).toEqual({ status: "unavailable" });
});

it.each([
  { url: reordered.href },
  { command: `curl '${reordered.href}'; echo "$?"` },
])("preserves the normalized CC file comparison", (input) => {
  const receipt = redactSessionReadEvidence({ messages: [
    { line_no: 1, record: { message: trace(original).messages[0] } },
    { line_no: 2, record: { message: { role: "assistant", content: [{ type: "tool_use", input }] } } },
  ] }, true);
  expect(receipt.url_argument_comparisons).toEqual([{ status: "match", source_line: 1, call_line: 2, call_index: 0 }]);
});
