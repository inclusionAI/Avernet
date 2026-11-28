import { describe, expect, it } from "vitest";
import { redactLogText } from "../redact-log-text.js";
import { boundedLogValue } from "../bounded-log-value.js";

describe("bounded diagnostic text redaction", () => {
  it.each([
    "/api/insight/v1/evidence-access/FAKE",
    "?next=%2fapi%2finsight%2fv1%2fEvidence-Access%2fFAKE",
    "%3FSignature%3DFAKE",
    "?X-Amz-Signature=FAKE",
    "?sig=FAKE",
    "?X-Goog-Signature=FAKE",
    "?X-OSS-Signature=FAKE",
    "%253fsiGNature%253dFAKE",
    "%25252525253FSignature%25252525253DFAKE",
    "/Evidence-Access/value%20FAKE",
    "?sig=value%2526FAKE",
    "reporter%40example.invalid/FAKE%80",
  ])("protects raw/encoded capability %s through both shared APIs", text => {
    for (const output of [redactLogText(text), boundedLogValue(text)]) {
      expect(output).not.toContain("FAKE");
      expect(output).toContain("REDACTED");
    }
  });

  it("preserves safe text, exact stack whitespace, console format tokens and Unicode", () => {
    const text = "Error: 😀 upstream %s %d %j %%\r\n    at origin (%s:%d:%j:%%)\n\ttrailing  \n";
    expect(redactLogText(text)).toBe(text);
  });

  it("never exposes a clipped credential prefix or splits a surrogate pair", () => {
    for (const text of ["ok /api/insight/v1/evidence-access/FAKE" + "x".repeat(10_000),
      "ok %3FSignature%3DFAKE" + "x".repeat(10_000), "😀 ".repeat(10_000)]) {
      for (const maxChars of [4, 16, 32, 128]) {
        const output = redactLogText(text, maxChars);
        expect(output.length).toBeLessThanOrEqual(maxChars);
        expect(output).not.toContain("FAKE");
        expect(output).not.toMatch(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u);
      }
    }
  });

  it("retains unambiguous ASCII escapes and masks decoded email addresses", () => {
    expect(redactLogText("phase%2Done /%64one")).toBe("phase-one /done");
    expect(redactLogText("contact=fixture%2540example.invalid")).toBe("contact=[REDACTED_EMAIL]");
  });

  it("bounds the scan even if a caller requests a larger preview", () => {
    const label = "workflow.preview.".repeat(250);
    const text = `${label} ready\n`.repeat(12);
    const start = process.cpuUsage();
    const logged = redactLogText(text, text.length);
    const used = process.cpuUsage(start);
    expect(logged).toContain(label);
    expect(logged.length).toBeLessThanOrEqual(16 * 1024);
    expect(logged.endsWith("...[truncated]")).toBe(true);
    expect(used.user + used.system).toBeLessThan(500_000);
  });

  it("protects signature keys and encoded capability property names", () => {
    const output = JSON.stringify(boundedLogValue({ Signature: "FAKE", sig: "FAKE",
      ["%3FSignature%3DFAKE"]: "value" }));
    expect(output).not.toContain("FAKE");
    expect(output).toContain("REDACTED");
  });
});
