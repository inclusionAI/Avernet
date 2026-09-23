import { describe, expect, it } from "vitest";
import {
  diagnoseSessionFilterSystemArgs,
  normalizeDiagnoseSessionFilter,
  parseNodeCommandYaml,
} from "../command.js";

describe("Diagnose Session filter command boundary", () => {
  it("normalizes multiple identifiers with stable deduplication", () => {
    const filter = normalizeDiagnoseSessionFilter({
      mode: "explicit",
      sessionIdentifiers: [" id-1 ", "agent:main:one", "id-1"],
    });
    expect(filter).toEqual({
      mode: "explicit",
      sessionIdentifiers: ["id-1", "agent:main:one"],
    });
    expect(diagnoseSessionFilterSystemArgs(filter)).toEqual([
      ["session-identifier", "'id-1'"],
      ["session-identifier", "'agent:main:one'"],
    ]);
  });

  it("rejects empty, oversized, and custom-command-owned selectors", () => {
    expect(() => normalizeDiagnoseSessionFilter({ mode: "explicit", sessionIdentifiers: [] })).toThrow(/1 到 20/);
    expect(() => normalizeDiagnoseSessionFilter({ mode: "explicit", sessionIdentifiers: Array.from({ length: 21 }, (_, index) => `id-${index}`) })).toThrow(/1 到 20/);
    expect(() => parseNodeCommandYaml('version: "1.0"\ncommand: /clawevolve-diagnose --session-identifier id-1\n', "diagnose")).toThrow(/系统参数/);
  });
});
