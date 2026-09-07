import { describe, expect, it } from "vitest";
import { timestampForDb } from "../insight-improvement-repository.js";

describe("Insight improvement database timestamps", () => {
  const handledAt = new Date("2026-08-21T07:30:00.000Z");

  it("uses a database timestamp for ZDAS TIMESTAMP columns", () => {
    expect(timestampForDb("zdas", handledAt)).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
  });

  it("keeps SQLite timestamps as epoch seconds", () => {
    expect(timestampForDb("sqlite", handledAt)).toBe(1787297400);
  });
});
