import { Buffer } from "node:buffer";
import { describe, expect, it } from "vitest";
import {
  RUN_LOG_MESSAGE_MAX_BYTES,
  RUN_LOG_MESSAGE_TRUNCATION_MARKER,
  truncateRunLogMessage,
} from "../run-log-message.js";

const limit = 65_535;
const marker = "\n...[truncated]";
const budget = limit - Buffer.byteLength(marker, "utf8");

describe("truncateRunLogMessage", () => {
  it("uses the storage byte limit and the required marker", () => {
    expect(RUN_LOG_MESSAGE_MAX_BYTES).toBe(limit);
    expect(RUN_LOG_MESSAGE_TRUNCATION_MARKER).toBe(marker);
  });

  it.each(["", "hello", "中文日志", "🙂🚀", "a\n\u0000b", marker])(
    "preserves short messages exactly: %j",
    (message) => {
      expect(truncateRunLogMessage(message)).toBe(message);
    },
  );

  it.each(["a", "é", "中", "🙂"])(
    "preserves %s messages at and immediately below the byte limit",
    (character) => {
      const width = Buffer.byteLength(character, "utf8");
      for (const bytes of [limit - 1, limit]) {
        const message = character.repeat(Math.floor(bytes / width)) + "a".repeat(bytes % width);
        expect(Buffer.byteLength(message, "utf8")).toBe(bytes);
        expect(truncateRunLogMessage(message)).toBe(message);
      }
    },
  );

  it.each(["a", "é", "中", "🙂"])(
    "truncates %s messages one byte over the limit including marker bytes",
    (character) => {
      const width = Buffer.byteLength(character, "utf8");
      const bytes = limit + 1;
      const message = character.repeat(Math.floor(bytes / width)) + "a".repeat(bytes % width);
      const result = truncateRunLogMessage(message);
      expect(Buffer.byteLength(message, "utf8")).toBe(bytes);
      expect(result).toBe(character.repeat(Math.floor(budget / width)) + marker);
      expect(Buffer.byteLength(result, "utf8")).toBeLessThanOrEqual(limit);
    },
  );

  it.each(["a", "é", "中", "🙂"])(
    "reserves marker space for large %s messages",
    (character) => {
      const width = Buffer.byteLength(character, "utf8");
      const result = truncateRunLogMessage(character.repeat(limit + 1));
      expect(result).toBe(character.repeat(Math.floor(budget / width)) + marker);
      expect(Buffer.byteLength(result, "utf8")).toBe(limit);
    },
  );

  it.each(["é", "中", "🙂"])(
    "never splits %s at any byte position of the cutoff",
    (character) => {
      const width = Buffer.byteLength(character, "utf8");
      for (let remaining = 0; remaining < width; remaining += 1) {
        const prefix = "a".repeat(budget - remaining);
        const message = prefix + character + "z".repeat(32);
        const result = truncateRunLogMessage(message);
        expect(result).toBe(prefix + marker);
        expect(Buffer.byteLength(result, "utf8")).toBe(limit - remaining);
        expect(Buffer.from(result, "utf8").toString("utf8")).toBe(result);
        expect(result).not.toContain("\uFFFD");
      }
    },
  );

  it("retains a complete mixed-width codepoint that exactly fits", () => {
    const prefix = "a".repeat(budget - 9) + "é中🙂";
    const result = truncateRunLogMessage(prefix + "z".repeat(32));
    expect(result).toBe(prefix + marker);
    expect(Buffer.byteLength(result, "utf8")).toBe(limit);
  });

  it.each(["a", "中文", "🙂", "a中🙂"])(
    "is idempotent for oversized %s messages",
    (character) => {
      const result = truncateRunLogMessage(character.repeat(limit + 1));
      expect(truncateRunLogMessage(result)).toBe(result);
      expect(result.endsWith(marker)).toBe(true);
      expect(Buffer.byteLength(result, "utf8")).toBeLessThanOrEqual(limit);
    },
  );
});
