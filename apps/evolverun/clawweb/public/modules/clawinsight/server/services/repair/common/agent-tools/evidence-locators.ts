import { posix } from "node:path";

const MAX_EVIDENCE_LOCATORS = 512;
const MAX_LOCATOR_LENGTH = 1_024;
const ABSOLUTE_PATH = /(?<![A-Za-z0-9_.:/-])(\/[^\s\0\r\n"'`<>|;,()\[\]{}]+)/gu;

function normalizedAbsolutePath(value: string): string | null {
  if (!value.startsWith("/") || value.length > MAX_LOCATOR_LENGTH || /[\0\r\n]/u.test(value)) return null;
  if (value.split("/").some((segment) => segment === "." || segment === "..")) return null;
  const normalized = posix.normalize(value);
  return normalized === "/" || !normalized.startsWith("/") ? null : normalized;
}

/** Extract exact filesystem locators from an already-redacted, typed evidence field. */
export function evidenceLocatorsFromText(...values: string[]): string[] {
  const locators = new Set<string>();
  for (const value of values) {
    for (const match of value.matchAll(ABSOLUTE_PATH)) {
      let candidate = String(match[1] ?? "").replace(/[.:\]]+$/u, "");
      candidate = candidate.replace(/:\d+(?::\d+)?$/u, "");
      const normalized = normalizedAbsolutePath(candidate);
      if (normalized) locators.add(normalized);
      if (locators.size >= MAX_EVIDENCE_LOCATORS) return [...locators];
    }
  }
  return [...locators];
}
