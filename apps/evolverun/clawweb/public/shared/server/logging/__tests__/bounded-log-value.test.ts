import { describe, expect, it, vi } from "vitest";
import { boundedLogValue } from "../bounded-log-value.js";

function entryCount(value: unknown): number {
  if (!value || typeof value !== "object") return 0;
  return Object.values(value).reduce<number>((count, child) => count + 1 + entryCount(child), 0);
}

describe("bounded log value previews", () => {
  it("defaults to a 16 KiB character budget, depth six and 100 GLOBAL visited entries", () => {
    const wide = Object.fromEntries(Array.from({ length: 1000 }, (_, i) => ["field" + i, { value: i }]));
    const result = boundedLogValue(wide);
    expect(entryCount(result)).toBeLessThanOrEqual(100);
    expect(JSON.stringify(result).length).toBeLessThanOrEqual(16 * 1024);
    expect(JSON.stringify(boundedLogValue("x ".repeat(50_000))).length).toBeLessThanOrEqual(16 * 1024);
    let deep: unknown = { final: "do not reach" };
    for (let i = 0; i < 20; i++) deep = { next: deep };
    const text = JSON.stringify(boundedLogValue(deep));
    expect(text).toContain("[TRUNCATED_DEPTH]");
    expect(text).not.toContain("do not reach");
    expect(entryCount(boundedLogValue(wide, { maxEntries: 7 }))).toBeLessThanOrEqual(7);
  });

  it("stops reading descendants once the global traversal budget is spent", () => {
    const reads: string[] = [];
    const child = new Proxy(Object.fromEntries(Array.from({ length: 1000 }, (_, i) => ["field" + i, i])), {
      getOwnPropertyDescriptor(target, key) {
        reads.push(String(key));
        return Reflect.getOwnPropertyDescriptor(target, key);
      },
    });
    const result = boundedLogValue({ first: child, neverReached: child }, { maxEntries: 4 });
    expect(entryCount(result)).toBe(4); // root key plus just three child keys
    expect(result).not.toHaveProperty("neverReached");
    expect(new Set(reads).size).toBeLessThanOrEqual(4);
  });

  it("keeps the total escaped JSON size bounded across tiny budgets, nested containers and Unicode", () => {
    const unicode = '😀𝄞漢字"\\\n\r\t\u0000\ud800\udc00'.repeat(1000);
    const values = [unicode, [unicode, unicode], { key: unicode, nested: [1, true, null, unicode] },
      [undefined, Symbol("s"), BigInt(2), NaN, Infinity], { ["q\"\\\n".repeat(20)]: unicode }];
    for (let maxChars = 4; maxChars < 256; maxChars++) {
      for (const value of values) {
        const captured = boundedLogValue(value, { maxChars });
        const encoded = JSON.stringify(captured);
        expect(encoded.length, `budget=${maxChars}`).toBeLessThanOrEqual(maxChars);
        expect(() => JSON.parse(encoded)).not.toThrow();
      }
    }
  });

  it("never cuts a surrogate pair when clipping valid Unicode", () => {
    for (let maxChars = 4; maxChars < 100; maxChars++) {
      const encoded = JSON.stringify(boundedLogValue("😀".repeat(1000), { maxChars }));
      expect(encoded.length).toBeLessThanOrEqual(maxChars);
      expect(encoded).not.toMatch(/\\u[dD][89aAbBcCdDeEfF][0-9a-fA-F]{2}/);
      expect(encoded).not.toContain("�");
    }
    expect(JSON.stringify(boundedLogValue("\ud800", { maxChars: 8 }))).toBe('"\\ud800"');
  });

  it("handles cycles and repeated references without treating siblings as cycles", () => {
    const child = { value: 1 };
    const input: Record<string, unknown> = { left: child, right: child };
    input.self = input;
    expect(boundedLogValue(input)).toEqual({ left: { value: 1 }, right: { value: 1 }, self: "[Circular]" });
  });

  it("never invokes getters, inherited accessors, toJSON or coercion hooks", () => {
    const getter = vi.fn(() => { throw new Error("getter ran"); });
    const toJSON = vi.fn(() => { throw new Error("toJSON ran"); });
    const input = Object.create(Object.defineProperty({}, "inherited", { enumerable: true, get: getter }));
    Object.defineProperty(input, "value", { enumerable: true, get: getter });
    Object.defineProperty(input, "hidden", { enumerable: false, get: getter });
    input.toJSON = toJSON;
    input[Symbol.toPrimitive] = getter;
    const array = [1, , 3];
    Object.defineProperty(array, 2, { get: getter });
    const encoded = JSON.stringify(boundedLogValue({ input, array, big: BigInt(10), fn: getter, symbol: Symbol("s") }));
    expect(encoded).toContain("[Getter]");
    expect(encoded).toContain("[empty]");
    expect(encoded).toContain("[bigint]");
    expect(encoded).not.toContain("inherited");
    expect(getter).not.toHaveBeenCalled();
    expect(toJSON).not.toHaveBeenCalled();
  });

  it("preserves own __proto__ safely without prototype pollution or inherited values", () => {
    const input = JSON.parse('{"__proto__":{"polluted":true},"constructor":{"prototype":{"polluted":true}},"value":2}');
    const result = boundedLogValue(input) as Record<string, unknown>;
    expect(Object.getPrototypeOf(result)).toBeNull();
    expect(Object.getPrototypeOf(result.__proto__)).toBeNull();
    expect(Object.hasOwn(result, "__proto__")).toBe(true);
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
    expect(JSON.parse(JSON.stringify(result))).toEqual(input);
  });

  it("redacts secrets/business bodies before touching children, including caller-specified keys", () => {
    const secretChild = new Proxy({}, { ownKeys() { throw new Error("must not traverse secret"); } });
    const custom = /privateField/gy;
    custom.lastIndex = 3;
    const encoded = JSON.stringify(boundedLogValue({ authorization: secretChild, Api_Key: "not-for-logs",
      messages: secretChild, privateField: secretChild, diagnostic: "Bearer token-example-secret", ok: 1 }, { redactKeys: custom }));
    expect(encoded).toContain("[REDACTED]");
    expect(encoded).toContain("[REDACTED_BUSINESS_BODY]");
    expect(encoded).not.toContain("not-for-logs");
    expect(encoded).not.toContain("token-example-secret");
    expect(encoded).not.toContain("Unserializable");
    expect(custom.lastIndex).toBe(3);
  });

  it("does not expose a partial token at a large string's sampling boundary", () => {
    const secret = "token=" + "s".repeat(50_000);
    const encoded = JSON.stringify(boundedLogValue("diagnostic " + secret, { maxChars: 100 }));
    expect(encoded).not.toContain("sssss");
    expect(encoded.length).toBeLessThanOrEqual(100);
  });

  it("ignores huge property names and survives revoked/throwing proxies within budget", () => {
    const getter = vi.fn();
    const longKey = "x".repeat(100_000);
    const input = Object.defineProperty({}, longKey, { enumerable: true, get: getter });
    expect(boundedLogValue(input)).toEqual({});
    expect(getter).not.toHaveBeenCalled();
    const revoked = Proxy.revocable({}, {}); revoked.revoke();
    const hostile = new Proxy({}, { ownKeys() { throw new Error("blocked"); } });
    for (const value of [revoked.proxy, hostile, { nested: hostile }]) {
      for (const maxChars of [4, 10, 25, 100]) {
        expect(JSON.stringify(boundedLogValue(value, { maxChars })).length).toBeLessThanOrEqual(maxChars);
      }
    }
  });

  it("supports explicit zero traversal/depth and safe fallback for invalid options", () => {
    expect(boundedLogValue({ a: 1 }, { maxEntries: 0 })).toEqual({});
    expect(boundedLogValue([1], { maxDepth: 0 })).toBe("[TRUNCATED_DEPTH]");
    expect(boundedLogValue("ok", { maxChars: -1, maxDepth: NaN, maxEntries: -1 })).toBe("ok");
    let deep: unknown = 1;
    for (let i = 0; i < 1000; i++) deep = [deep];
    expect(() => JSON.stringify(boundedLogValue(deep, { maxDepth: 1_000_000, maxEntries: 100_000 }))).not.toThrow();
  });
});
