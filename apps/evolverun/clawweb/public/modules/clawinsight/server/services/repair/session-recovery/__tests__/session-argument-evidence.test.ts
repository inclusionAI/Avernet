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

it("keeps distinct source links and tool arguments ambiguous even if they normalize to the same URL", () => {
  expect(compareTraceUrlArguments(trace(original, encoded), { url: original.href })).toEqual({ status: "unavailable" });
  expect(compareTraceUrlArguments(trace(original), { urls: [original.href, reordered.href] })).toEqual({ status: "unavailable" });
});

it("leaves redacted trace inputs unavailable", () => {
  expect(compareTraceUrlArguments(trace(masked), { url: original.href })).toEqual({ status: "unavailable" });
});

it("preserves the normalized CC file comparison", () => {
  const receipt = redactSessionReadEvidence({ messages: [
    { line_no: 1, record: { message: trace(original).messages[0] } },
    { line_no: 2, record: { message: { role: "assistant", content: [{ type: "tool_use", input: { url: reordered.href } }] } } },
  ] }, true);
  expect(receipt.url_argument_comparisons).toEqual([{ status: "match", source_line: 1, call_line: 2, call_index: 0 }]);
});
