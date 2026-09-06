import { describe, expect, it } from "vitest";
import { mergeClawWebConfig } from "../config-loader.js";

describe("mergeClawWebConfig", () => {
  it("merges base and profile mappings without replacing siblings", () => {
    expect(mergeClawWebConfig(
      { database: { mode: "sqlite", sqlite: { path: "base.db" } } },
      { database: { mode: "zdas" } },
    )).toEqual({ database: { mode: "zdas", sqlite: { path: "base.db" } } });
  });

  it("ignores prototype mutation keys", () => {
    const layer = JSON.parse('{"__proto__":{"polluted":true},"safe":true}') as Record<string, unknown>;
    expect(mergeClawWebConfig(layer)).toEqual({ safe: true });
    expect(({} as { polluted?: boolean }).polluted).toBeUndefined();
  });
});
