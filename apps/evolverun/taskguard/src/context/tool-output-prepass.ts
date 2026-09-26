/**
 * Tool output preprocessing for session context compression.
 *
 * Applies pattern-based compression rules to verbose tool results before
 * they enter the session JSONL file. Inspired by Emu's 15-rule approach,
 * adapted for the ClawMind runtime's tool output patterns.
 *
 * Zero-cost: no LLM calls, pure pattern matching and truncation.
 *
 * @module context/tool-output-prepass
 */

import type { SessionMessage } from "./session-reader.js";
import { estimateTextTokens } from "./token-counter.js";
import { detectContentType } from "./content-detector.js";

// ── Rule Definition ──

/** A compression rule that matches tool output and produces a shorter version. */
export type ToolOutputRule = {
  /** Human-readable name for logging and stats. */
  name: string;
  /** Regex to test against the tool output. */
  pattern: RegExp;
  /** Compression function: takes the original output, returns compressed version. */
  compress: (output: string) => string;
  /** Short description for documentation. */
  description: string;
};

/** Result of applying tool output prepass to a session. */
export type ToolOutputPrepassResult = {
  /** Messages after compression (new array, no mutation). */
  messages: SessionMessage[];
  /** Number of tool results that were compressed. */
  compressedCount: number;
  /** Estimated tokens saved by compression. */
  savedTokenEstimate: number;
  /** Names of rules that were actually applied. */
  rulesApplied: string[];
};

// ── Compression Rules ──

/**
 * Rule: Compress test output (npm/yarn/pnpm test, vitest, jest, pytest, go test).
 * Keep only FAIL lines and the summary.
 */
const testOutputRule: ToolOutputRule = {
  name: "test-output",
  pattern: /(?:npm|yarn|pnpm)\s+(test|run\s+test)|vitest|jest|pytest\s|python\s+-m\s+unittest|go\s+test/,
  description: "Compress test runner output to FAIL/PASS summary",
  compress: (output: string): string => {
    const lines = output.split("\n");
    const summaryLines: string[] = [];
    const failLines: string[] = [];

    for (const line of lines) {
      const trimmed = line.trim();
      // Keep summary lines
      if (
        /Tests?:\s+\d+\s+(passed|failed|skipped)/i.test(trimmed)
        || /Test Suites?:\s/.test(trimmed)
        || /✅|❌|✓|✗|PASS|FAIL|passed|failed/i.test(trimmed)
        || /Ran \d+ test/i.test(trimmed)
        || /ok\s+\d+/.test(trimmed)
        || /not ok\s+\d+/.test(trimmed)
        || /FAIL\s/i.test(trimmed)
        || /ERROR/i.test(trimmed)
        || /✕|⨯/u.test(trimmed)
      ) {
        summaryLines.push(line);
      }
      // Keep FAIL detail lines
      if (/FAIL|✕|⨯|not ok|AssertionError/i.test(trimmed)) {
        failLines.push(line);
      }
    }

    if (summaryLines.length > 0 || failLines.length > 0) {
      const parts: string[] = ["[test-output-compressed]"];
      if (failLines.length > 0) {
        parts.push(...failLines.slice(0, 20));
      }
      if (summaryLines.length > 0) {
        parts.push(...summaryLines.slice(0, 10));
      }
      const result = parts.join("\n");
      return result.length < output.length ? result : output;
    }

    // If no recognized patterns, just take the last 20 lines
    return truncateOutput(output, 30);
  },
};

/**
 * Rule: Compress build output (npm/yarn/pnpm build, cargo build, make, dotnet build).
 * Keep only errors and warnings.
 */
const buildOutputRule: ToolOutputRule = {
  name: "build-output",
  pattern: /(?:npm|yarn|pnpm)\s+(run\s+)?build|cargo\s+build|make\s|dotnet\s+build|gradle\s+build/,
  description: "Compress build output to errors and warnings only",
  compress: (output: string): string => {
    const lines = output.split("\n");
    const importantLines = lines.filter((line) => {
      const trimmed = line.trim();
      return (
        /error/i.test(trimmed)
        || /warning/i.test(trimmed)
        || /FAIL/i.test(trimmed)
        || /Build succeeded/i.test(trimmed)
        || /Build FAILED/i.test(trimmed)
        || /Compiling/i.test(trimmed)
        || trimmed.length === 0 // Keep blank lines for readability
      );
    });

    if (importantLines.length < lines.length) {
      const result = [
        `[build-output-compressed: ${lines.length} lines → ${importantLines.length} lines]`,
        ...importantLines.slice(0, 50),
      ].join("\n");
      return result.length < output.length ? result : output;
    }

    return output;
  },
};

/**
 * Rule: Compress install output (npm/yarn/pnpm install, pip install).
 * Keep only errors and warnings.
 */
const installOutputRule: ToolOutputRule = {
  name: "install-output",
  pattern: /(?:npm|yarn|pnpm)\s+install|pip\s+install|cargo\s+install/,
  description: "Compress install output to errors only",
  compress: (output: string): string => {
    const lines = output.split("\n");
    const importantLines = lines.filter((line) => {
      const trimmed = line.trim();
      return (
        /error/i.test(trimmed)
        || /warn/i.test(trimmed)
        || /added \d+ packages/i.test(trimmed)
        || /removed \d+ packages/i.test(trimmed)
        || /up to date/i.test(trimmed)
        || /Successfully installed/i.test(trimmed)
        || trimmed.length === 0
      );
    });

    if (importantLines.length < lines.length) {
      const result = [
        `[install-output-compressed: ${lines.length} lines → ${importantLines.length} lines]`,
        ...importantLines.slice(0, 20),
      ].join("\n");
      return result.length < output.length ? result : output;
    }

    return output;
  },
};

/**
 * Rule: Compress lint output (eslint, tsc, rubocop).
 * Keep error count and first few errors.
 */
const lintOutputRule: ToolOutputRule = {
  name: "lint-output",
  pattern: /eslint|tsc\s|--noEmit|rubocop|flake8|pylint/,
  description: "Compress lint output to error count and first errors",
  compress: (output: string): string => {
    const lines = output.split("\n");
    const errorLines = lines.filter((line) =>
      /error|Error|✕|✗|warning/i.test(line),
    );

    if (errorLines.length > 0) {
      const result = [
        `[lint-output-compressed: ${errorLines.length} errors/warnings in ${lines.length} lines]`,
        ...errorLines.slice(0, 10),
        ...(errorLines.length > 10 ? [`... and ${errorLines.length - 10} more`] : []),
      ].join("\n");
      return result.length < output.length ? result : output;
    }

    return truncateOutput(output, 15);
  },
};

/**
 * Rule: Compress git log output.
 * Replace verbose git log with --oneline.
 */
const gitLogRule: ToolOutputRule = {
  name: "git-log",
  pattern: /git\s+log/,
  description: "Compress verbose git log to oneline format",
  compress: (output: string): string => {
    const lines = output.split("\n");
    if (lines.length > 20) {
      // Try to extract commit hashes and first line of each commit
      const onelineLines: string[] = [];
      for (const line of lines) {
        const match = line.match(/^([a-f0-9]{7,40})\s+(.*)/);
        if (match) {
          onelineLines.push(`${match[1]} ${match[2]}`);
        }
      }

      if (onelineLines.length > 0) {
        const kept = onelineLines.slice(0, 20);
        const result = [
          `[git-log-compressed: ${onelineLines.length} commits, showing last ${kept.length}]`,
          ...kept,
          ...(onelineLines.length > 20 ? [`... and ${onelineLines.length - 20} more`] : []),
        ].join("\n");
        return result.length < output.length ? result : output;
      }
    }

    return truncateOutput(output, 30);
  },
};

/**
 * Rule: Compress find command output.
 * Add head -n 30 if not already limited.
 */
const findOutputRule: ToolOutputRule = {
  name: "find-output",
  pattern: /find\s+/,
  description: "Limit find output to first 30 results",
  compress: (output: string): string => {
    // If the find command already has head/limit, don't compress
    if (/find\s+.*\|\s*head/.test(output) || /find\s+.*-maxdepth/.test(output)) {
      return output;
    }
    return truncateOutput(output, 30);
  },
};

/**
 * Rule: Compress cat/Read output for large files.
 * If >100 lines, keep head + tail with line count.
 */
const catOutputRule: ToolOutputRule = {
  name: "cat-read-output",
  pattern: /cat\s+|Read\s+(file|result|output)/i,
  description: "Compress large file output to head + tail",
  compress: (output: string): string => {
    return truncateLargeOutput(output, 80);
  },
};

/**
 * Rule: Compress Docker/Terraform output.
 */
const dockerTerraformRule: ToolOutputRule = {
  name: "docker-terraform",
  pattern: /docker\s+(build|run|push)|terraform\s+(plan|apply|show)/,
  description: "Compress Docker/Terraform output to key lines",
  compress: (output: string): string => {
    const lines = output.split("\n");
    const importantLines = lines.filter((line) => {
      const trimmed = line.trim();
      return (
        /error|Error|FAIL/i.test(trimmed)
        || /warning|Warning/i.test(trimmed)
        || /Step \d+\/\d+/i.test(trimmed)
        || /Successfully (built|tagged|pushed)/i.test(trimmed)
        || /Plan:/i.test(trimmed)
        || /Apply complete/i.test(trimmed)
        || /No changes/i.test(trimmed)
        || /Image.*built/i.test(trimmed)
        || trimmed.length === 0
      );
    });

    if (importantLines.length < lines.length) {
      const result = [
        `[infra-output-compressed: ${lines.length} lines → ${importantLines.length} lines]`,
        ...importantLines.slice(0, 30),
      ].join("\n");
      return result.length < output.length ? result : output;
    }

    return truncateOutput(output, 30);
  },
};

/**
 * Rule: Generic large output truncation.
 * If output >5000 chars, truncate to first 2000 chars + summary.
 */
const genericTruncationRule: ToolOutputRule = {
  name: "generic-truncation",
  pattern: /[\s\S]{5000,}/,
  description: "Truncate any tool output exceeding 5000 characters",
  compress: (output: string): string => {
    if (output.length <= 5000) return output;
    return truncateLargeOutput(output, 60);
  },
};

// ── Content-Aware Rules (Inspired by Claw Compactor's Fusion Pipeline) ──

/**
 * Rule: JSON array sampling (Ionizer-inspired).
 * When a tool result contains a large JSON array (>20 elements),
 * extract the schema (keys + types), keep a representative sample (5 items),
 * and produce a summary with statistics.
 *
 * Only applies when detectContentType returns "json" with isJsonArray=true.
 */
const jsonArraySamplerRule: ToolOutputRule = {
  name: "json-array-sampler",
  pattern: /^\s*\[/,  // Starts with '[' — fast pre-filter
  description: "Sample large JSON arrays: schema + representative items + statistics",
  compress: (output: string): string => {
    const detection = detectContentType(output);
    if (detection.type !== "json" || !detection.isJsonArray) return output;

    let parsed: unknown[];
    try {
      const obj = JSON.parse(output);
      if (!Array.isArray(obj)) return output;
      parsed = obj;
    } catch {
      return output;
    }

    const MIN_ARRAY_LENGTH = 20;
    const SAMPLE_SIZE = 5;

    if (parsed.length < MIN_ARRAY_LENGTH) return output;

    // Extract schema from all items
    const schema = extractArraySchema(parsed);
    const sample = parsed.slice(0, SAMPLE_SIZE);

    // Compute statistics
    const stats: Record<string, string> = {
      totalElements: String(parsed.length),
      sampleSize: String(sample.length),
    };

    // Numeric field statistics
    for (const [key, type] of Object.entries(schema)) {
      if (type === "number") {
        const values = parsed
          .map((item) => (item as Record<string, unknown>)[key])
          .filter((v): v is number => typeof v === "number");
        if (values.length > 0) {
          const min = Math.min(...values);
          const max = Math.max(...values);
          const avg = values.reduce((a, b) => a + b, 0) / values.length;
          stats[key] = `min=${min}, max=${max}, avg=${avg.toFixed(1)}`;
        }
      }
    }

    const parts: string[] = [
      `[json-array-compressed: ${parsed.length} elements → ${sample.length} sample + schema]`,
      `Schema: {${Object.entries(schema).map(([k, v]) => `${k}: ${v}`).join(", ")}}`,
      `Stats: {${Object.entries(stats).map(([k, v]) => `${k}: ${v}`).join(", ")}}`,
      `Sample:`,
      JSON.stringify(sample, null, 2),
      `... (${parsed.length - sample.length} more elements omitted)`,
    ];

    const result = parts.join("\n");
    return result.length < output.length ? result : output;
  },
};

/**
 * Rule: Log line folding (LogCrunch-inspired).
 * Identifies consecutive lines with identical or near-identical structure
 * (same log level + prefix + message template, varying parameters).
 * Collapses runs into a single representative line with a repeat count.
 * Lines containing error/failure/exception markers are always preserved.
 */
const logFoldRule: ToolOutputRule = {
  name: "log-fold",
  pattern: /(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2})|(?:TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\b/i,
  description: "Fold consecutive repetitive log lines with occurrence counts",
  compress: (output: string): string => {
    const detection = detectContentType(output);
    if (detection.type !== "log") return output;

    const lines = output.split("\n");
    if (lines.length < 5) return output; // Not enough lines to fold

    const folded: string[] = [];
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];
      const template = extractLogTemplate(line);

      // Count consecutive lines with the same template
      let count = 1;
      while (
        i + count < lines.length &&
        extractLogTemplate(lines[i + count]) === template &&
        !isErrorLine(lines[i + count]) // Never fold error lines
      ) {
        count++;
      }

      if (count >= 3) {
        // Fold this run
        folded.push(`${line}  [×${count}]`);
        i += count;
      } else {
        folded.push(line);
        i++;
      }
    }

    const result = folded.join("\n");
    return result.length < output.length ? result : output;
  },
};

/**
 * Rule: Diff context folding (DiffCrunch-inspired).
 * Folds runs of unchanged context lines in unified diffs, keeping only
 * changed lines (+/-) and a small context window (3 lines) around them.
 * Only applies when detectContentType returns "diff".
 */
const diffFoldRule: ToolOutputRule = {
  name: "diff-fold",
  pattern: /^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@/m,
  description: "Fold unchanged context lines in unified diffs",
  compress: (output: string): string => {
    const detection = detectContentType(output);
    if (detection.type !== "diff") return output;

    const CONTEXT_WINDOW = 3;
    const lines = output.split("\n");
    const result: string[] = [];
    let i = 0;

    // Track hunks and identify changed line positions
    const changedPositions = new Set<number>();
    for (let li = 0; li < lines.length; li++) {
      const line = lines[li];
      if (line.startsWith("+") && !line.startsWith("++") ||
          line.startsWith("-") && !line.startsWith("--")) {
        changedPositions.add(li);
      }
    }

    // Expand context window around changed positions
    const keepPositions = new Set<number>();
    for (const pos of changedPositions) {
      for (let offset = -CONTEXT_WINDOW; offset <= CONTEXT_WINDOW; offset++) {
        const p = pos + offset;
        if (p >= 0 && p < lines.length) {
          keepPositions.add(p);
        }
      }
    }

    // Always keep hunk headers and file headers
    for (let li = 0; li < lines.length; li++) {
      const line = lines[li];
      if (
        line.startsWith("@@") ||
        line.startsWith("diff ") ||
        line.startsWith("---") ||
        line.startsWith("+++") ||
        line.startsWith("Index: ") ||
        line.startsWith("===")
      ) {
        keepPositions.add(li);
      }
    }

    // Build output with folded regions
    let omittedCount = 0;
    for (let li = 0; li < lines.length; li++) {
      if (keepPositions.has(li)) {
        if (omittedCount > 0) {
          result.push(`@@ ${omittedCount} context lines omitted @@`);
          omittedCount = 0;
        }
        result.push(lines[li]);
      } else if (li > 0 && !lines[li - 1].startsWith("@@") && !lines[li - 1].startsWith("diff")) {
        // Count as a context line to fold
        omittedCount++;
      } else {
        // Header-adjacent line, keep it
        result.push(lines[li]);
      }
    }

    if (omittedCount > 0) {
      result.push(`@@ ${omittedCount} context lines omitted @@`);
    }

    const resultText = result.join("\n");
    return resultText.length < output.length ? resultText : output;
  },
};

// ── Rule Registry ──

/** All registered tool output compression rules, in priority order. */
const TOOL_OUTPUT_RULES: ToolOutputRule[] = [
  // Content-type aware rules (run first—more specific, higher value)
  jsonArraySamplerRule,
  logFoldRule,
  diffFoldRule,
  // Pattern-based rules (tool command matching)
  testOutputRule,
  buildOutputRule,
  installOutputRule,
  lintOutputRule,
  gitLogRule,
  findOutputRule,
  catOutputRule,
  dockerTerraformRule,
  genericTruncationRule, // Must be last — catches anything that slips through
];

// ── Read Deduplication ──

/** Cache entry for file read deduplication. */
type ReadCacheEntry = {
  content: string;
  timestamp: number;
  lineCount: number;
};

/** In-memory cache for detecting duplicate file reads. */
const readCache = new Map<string, ReadCacheEntry>();

/** Default TTL for read dedup cache (5 minutes). */
const DEFAULT_READ_DEDUP_TTL_MS = 300_000;

/**
 * Check if a file read is a duplicate of a recent read.
 * Returns the cached content if it's a duplicate, or undefined if it's new.
 */
function checkDuplicateRead(
  filePath: string,
  currentContent: string,
  ttlMs: number = DEFAULT_READ_DEDUP_TTL_MS,
): ReadCacheEntry | undefined {
  const cached = readCache.get(filePath);
  if (!cached) return undefined;

  const age = Date.now() - cached.timestamp;
  if (age > ttlMs) {
    readCache.delete(filePath);
    return undefined;
  }

  // Exact content match
  if (cached.content === currentContent) {
    return cached;
  }

  return undefined;
}

/**
 * Record a file read in the dedup cache.
 */
function recordRead(filePath: string, content: string): void {
  const lineCount = content.split("\n").length;
  readCache.set(filePath, {
    content,
    timestamp: Date.now(),
    lineCount,
  });
}

/**
 * Get the number of changed lines between two contents.
 */
function getChangedLines(oldContent: string, newContent: string): number {
  const oldLines = oldContent.split("\n");
  const newLines = newContent.split("\n");
  let changed = 0;

  const maxLen = Math.max(oldLines.length, newLines.length);
  for (let i = 0; i < maxLen; i++) {
    if (oldLines[i] !== newLines[i]) changed++;
  }

  return changed;
}

// ── Public API ──

/**
 * Apply tool output prepass compression to a list of session messages.
 *
 * Scans tool_result messages and applies matching compression rules.
 * Also detects and deduplicates repeated file reads.
 *
 * Returns a new messages array with compressed tool results (no mutation).
 */
export function applyToolOutputPrepass(
  messages: SessionMessage[],
  options?: {
    /** Maximum characters for a single tool result before truncation. Default: 5000 */
    maxResultChars?: number;
    /** TTL in ms for read dedup cache. Default: 300000 (5min) */
    readDedupTtlMs?: number;
  },
): ToolOutputPrepassResult {
  const maxResultChars = options?.maxResultChars ?? 5000;
  const readDedupTtlMs = options?.readDedupTtlMs ?? DEFAULT_READ_DEDUP_TTL_MS;

  let compressedCount = 0;
  let savedTokenEstimate = 0;
  const rulesApplied: string[] = [];
  const appliedRuleSet = new Set<string>();

  const result: SessionMessage[] = messages.map((msg) => {
    if (!msg.isToolResult) return msg;

    // Extract tool name and output
    const toolName = msg.toolName;
    const output = msg.text;

    // Check for duplicate reads
    if (toolName === "Read" || toolName === "read" || toolName === "cat") {
      const filePath = extractFilePath(output);
      if (filePath) {
        const cached = checkDuplicateRead(filePath, output, readDedupTtlMs);
        if (cached) {
          const lineCount = output.split("\n").length;
          const changedLines = getChangedLines(cached.content, output);
          if (changedLines === 0) {
            // Exact duplicate — replace with a compact reference
            const compressed = `[duplicate-read: ${filePath} (${lineCount} lines, unchanged since ${formatTimeAgo(cached.timestamp)})]`;
            compressedCount++;
            savedTokenEstimate += msg.tokenCount - estimateTextTokens(compressed) - 4;
            appliedRuleSet.add("duplicate-read");
            return modifyToolResultContent(msg, compressed);
          } else {
            // Changed file — show delta summary
            const compressed = `[changed-read: ${filePath} (${lineCount} lines, ${changedLines} lines changed since ${formatTimeAgo(cached.timestamp)})]\n${output.split("\n").slice(0, 10).join("\n")}\n... (${lineCount} lines total)`;
            compressedCount++;
            savedTokenEstimate += msg.tokenCount - estimateTextTokens(compressed) - 4;
            appliedRuleSet.add("delta-read");
            return modifyToolResultContent(msg, compressed);
          }
        }

        // Record this read for future dedup
        recordRead(filePath, output);
      }
    }

    // Apply pattern-based rules
    for (const rule of TOOL_OUTPUT_RULES) {
      if (rule.pattern.test(output) || (toolName && rule.pattern.test(toolName))) {
        const compressed = rule.compress(output);
        if (compressed.length < output.length) {
          compressedCount++;
          savedTokenEstimate += msg.tokenCount - estimateTextTokens(compressed) - 4;
          appliedRuleSet.add(rule.name);
          return modifyToolResultContent(msg, compressed);
        }
      }
    }

    // Generic size truncation (already covered by genericTruncationRule, but as a safety net)
    if (output.length > maxResultChars) {
      const compressed = truncateLargeOutput(output, 60);
      if (compressed.length < output.length) {
        compressedCount++;
        savedTokenEstimate += msg.tokenCount - estimateTextTokens(compressed) - 4;
        appliedRuleSet.add("size-truncation");
        return modifyToolResultContent(msg, compressed);
      }
    }

    return msg;
  });

  return {
    messages: result,
    compressedCount,
    savedTokenEstimate: Math.max(0, savedTokenEstimate),
    rulesApplied: Array.from(appliedRuleSet),
  };
}

// ── Utility Functions ──

/**
 * Extract a schema from a JSON array: keys → inferred types.
 * Used by the JSON array sampler rule.
 */
function extractArraySchema(items: unknown[]): Record<string, string> {
  const schema: Record<string, string> = {};
  const allKeys = new Set<string>();

  // Collect all keys from sampled items (first 10 for schema extraction)
  const sampleForSchema = items.slice(0, 10);
  for (const item of sampleForSchema) {
    if (item !== null && typeof item === "object" && !Array.isArray(item)) {
      for (const key of Object.keys(item as Record<string, unknown>)) {
        allKeys.add(key);
      }
    }
  }

  // Infer types
  for (const key of allKeys) {
    const types = new Set<string>();
    for (const item of sampleForSchema) {
      if (item !== null && typeof item === "object" && !Array.isArray(item)) {
        const val = (item as Record<string, unknown>)[key];
        types.add(inferType(val));
      }
    }
    schema[key] = Array.from(types).join("|");
  }

  return schema;
}

/**
 * Infer the type label for a JSON value.
 */
function inferType(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return `Array(${value.length})`;
  if (typeof value === "object") return `Object(${Object.keys(value as Record<string, unknown>).length})`;
  return typeof value;
}

/**
 * Extract a log line template for grouping similar lines.
 * Replaces variable parts (numbers, timestamps, hex IDs) with placeholders.
 * Used by the log fold rule.
 */
function extractLogTemplate(line: string): string {
  return line
    // Replace timestamps
    .replace(/\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?/g, "<TS>")
    .replace(/\d{2}:\d{2}:\d{2}(?:\.\d+)?/g, "<TIME>")
    // Replace hex/UUID
    .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi, "<UUID>")
    .replace(/0x[0-9a-f]+/gi, "<HEX>")
    // Replace numbers
    .replace(/\b\d+(\.\d+)?\b/g, "<N>")
    // Replace file paths (simple heuristic)
    .replace(/\/[\w./-]+\.\w{1,10}/g, "<PATH>")
    // Replace quoted strings
    .replace(/"[^"]{3,}"/g, "<STR>")
    .replace(/'[^']{3,}'/g, "<STR>");
}

/**
 * Check if a log line contains error/failure/exception markers.
 * These lines are never folded.
 */
function isErrorLine(line: string): boolean {
  return /error|fail|exception|traceback|panic|fatal|critical|abort/i.test(line);
}

/**
 * Truncate output to a maximum number of lines.
 * Keeps first half and last quarter with a summary in between.
 */
function truncateOutput(output: string, maxLines: number): string {
  const lines = output.split("\n");
  if (lines.length <= maxLines) return output;

  const headLines = Math.ceil(maxLines * 0.6);
  const tailLines = Math.floor(maxLines * 0.3);
  const head = lines.slice(0, headLines);
  const tail = lines.slice(-tailLines);

  return [
    ...head,
    `... (${lines.length} lines total, ${lines.length - headLines - tailLines} lines omitted)`,
    ...tail,
  ].join("\n");
}

/**
 * Truncate large output with head + tail pattern.
 * Shows beginning and end, with a summary of the omitted section.
 */
function truncateLargeOutput(output: string, maxHeadLines: number): string {
  const lines = output.split("\n");
  if (lines.length <= maxHeadLines) return output;

  const headLines = Math.ceil(maxHeadLines * 0.6);
  const tailLines = Math.floor(maxHeadLines * 0.3);
  const head = lines.slice(0, headLines);
  const tail = lines.slice(-tailLines);

  return [
    ...head,
    `... (${lines.length} lines total, ${lines.length - headLines - tailLines} lines omitted)`,
    ...tail,
  ].join("\n");
}

/**
 * Extract a file path from a tool result output.
 * Best-effort heuristic for Read/cat commands.
 */
function extractFilePath(output: string): string | undefined {
  // Common patterns:
  // "Contents of /path/to/file:"
  // "/path/to/file:"
  // "Read file: /path/to/file"
  // "cat /path/to/file"
  // "→ /path/to/file"
  const patterns = [
    /(?:contents of|read file|cat)\s+(\/[^\s:]+)/i,
    /^([^\s:]+\.ts|[^\s:]+\.js|[^\s:]+\.py|[^\s:]+\.json|[^\s:]+\.yaml|[^\s:]+\.md)/im,
    /→\s*(\/[^\s]+)/,
  ];

  for (const pattern of patterns) {
    const match = output.match(pattern);
    if (match?.[1]) return match[1];
  }

  return undefined;
}

/**
 * Format a timestamp as a relative time ago string.
 */
function formatTimeAgo(timestamp: number): string {
  const seconds = Math.floor((Date.now() - timestamp) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

/**
 * Modify a tool_result message's content.
 * Re-exported from session-reader for convenience.
 */
function modifyToolResultContent(
  msg: SessionMessage,
  newContent: string,
): SessionMessage {
  // Use the same implementation as in session-reader
  if (!msg.isToolResult) return msg;

  try {
    const parsed = JSON.parse(msg.raw) as Record<string, unknown>;
    const message = typeof parsed.message === "object" && parsed.message !== null
      ? { ...(parsed.message as Record<string, unknown>) }
      : parsed;

    const content = Array.isArray(message.content)
      ? (message.content as Record<string, unknown>[]).map((block) => {
          if ((block as Record<string, unknown>).type === "tool_result") {
            return { ...block, content: newContent };
          }
          return block;
        })
      : message.content;

    const modifiedMessage = { ...message, content };
    const raw = JSON.stringify({ ...parsed, message: modifiedMessage });

    return {
      ...msg,
      raw,
      text: newContent,
      tokenCount: estimateTextTokens(newContent) + 4,
      contentBlocks: [{ type: "text" as const, text: newContent }],
    };
  } catch {
    return {
      ...msg,
      text: newContent,
      tokenCount: estimateTextTokens(newContent) + 4,
    };
  }
}