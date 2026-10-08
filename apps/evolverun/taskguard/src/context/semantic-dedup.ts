/**
 * Cross-message semantic deduplication using SimHash fingerprints.
 *
 * Inspired by Claw Compactor's SemanticDedup stage: detects near-duplicate
 * content blocks across conversation turns using 64-bit SimHash with
 * Hamming distance comparison. When two messages are sufficiently similar
 * (distance ≤ threshold), the later one is replaced with a compact reference.
 *
 * This catches:
 * - Repeated file reads with identical content
 * - Re-sent error messages
 * - Tool results that contain overlapping output (e.g., re-running a test)
 * - Duplicate system prompt fragments across turns
 *
 * @module context/semantic-dedup
 */

import type { SessionMessage } from "./session-reader.js";
import { modifyToolResultContent } from "./session-reader.js";
import { estimateTextTokens } from "./token-counter.js";

// ── Types ──

/** Result of cross-message semantic deduplication. */
export type SemanticDedupResult = {
  /** Messages after deduplication (new array, no mutation). */
  messages: SessionMessage[];
  /** Number of messages replaced with references. */
  dedupedCount: number;
  /** Estimated tokens saved by deduplication. */
  tokensSaved: number;
  /** Pairs of deduplicated messages (index → similar-to index). */
  dedupPairs: Array<{ from: number; similarTo: number; distance: number }>;
};

// ── Constants ──

/** Default Hamming distance threshold for near-duplicate detection. */
const DEFAULT_SIMHASH_THRESHOLD = 3;

/** Minimum message length (chars) to compute SimHash. Shorter messages are skipped. */
const MIN_MESSAGE_LENGTH = 50;

/** Minimum token count to consider for dedup. Messages with fewer tokens are skipped. */
const MIN_TOKEN_COUNT = 20;

// ── SimHash ──

/**
 * Compute a 64-bit SimHash fingerprint for a text string.
 *
 * Algorithm:
 * 1. Tokenize the text into words
 * 2. Hash each word using a simple polynomial hash
 * 3. For each bit position, increment if the hash has a 1, decrement if 0
 * 4. Take the sign of each bit position to form the final hash
 *
 * The resulting fingerprint has the property that similar texts produce
 * similar hashes (small Hamming distance).
 */
export function simhashFingerprint(text: string): bigint {
  const tokens = tokenize(text);
  if (tokens.length === 0) return 0n;

  // 64-bit vector of bit counts
  const bits = new Int16Array(64);

  for (const token of tokens) {
    const hash = fnv1aHash(token);
    for (let i = 0; i < 64; i++) {
      if ((hash & (1n << BigInt(i))) !== 0n) {
        bits[i]++;
      } else {
        bits[i]--;
      }
    }
  }

  // Build the final hash from bit signs
  let result = 0n;
  for (let i = 0; i < 64; i++) {
    if (bits[i] > 0) {
      result |= (1n << BigInt(i));
    }
  }

  return result;
}

/**
 * Compute the Hamming distance between two SimHash fingerprints.
 * The Hamming distance is the number of differing bits.
 */
export function hammingDistance(a: bigint, b: bigint): number {
  let xor = a ^ b;
  let count = 0;
  while (xor !== 0n) {
    count += Number(xor & 1n);
    xor >>= 1n;
  }
  return count;
}

// ── Tokenization ──

/**
 * Tokenize text for SimHash computation.
 * Splits on non-alphanumeric characters, filters short tokens,
 * and lowercases for case-insensitive comparison.
 */
function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .split(/[^a-z0-9一-鿿]+/)
    .filter((token) => token.length >= 3);
}

/**
 * FNV-1a hash for strings, producing a 64-bit BigInt.
 * Simple, fast, and well-distributed.
 */
function fnv1aHash(str: string): bigint {
  const FNV_OFFSET = 14695981039346656037n;
  const FNV_PRIME = 1099511628211n;
  const MASK = (1n << 64n) - 1n;

  let hash = FNV_OFFSET;
  for (let i = 0; i < str.length; i++) {
    hash ^= BigInt(str.charCodeAt(i));
    hash = (hash * FNV_PRIME) & MASK;
  }

  return hash;
}

// ── Deduplication ──

/**
 * Perform cross-message semantic deduplication on a list of session messages.
 *
 * Strategy:
 * 1. Compute SimHash fingerprint for each non-system message
 * 2. Compare each message's fingerprint against all previously seen fingerprints
 * 3. If the Hamming distance ≤ threshold, the later message is a near-duplicate
 * 4. Replace near-duplicate messages with a compact reference
 *
 * Messages below MIN_MESSAGE_LENGTH or MIN_TOKEN_COUNT are skipped (too short
 * to meaningfully compare). System messages are always preserved.
 *
 * Returns a new messages array (immutable — input is not mutated).
 */
export function deduplicateMessages(
  messages: readonly SessionMessage[],
  threshold: number = DEFAULT_SIMHASH_THRESHOLD,
): SemanticDedupResult {
  const result: SessionMessage[] = [];
  const fingerprints: bigint[] = [];  // Parallel to result indices
  let dedupedCount = 0;
  let tokensSaved = 0;
  const dedupPairs: SemanticDedupResult["dedupPairs"] = [];

  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];

    // Always preserve system messages
    if (msg.role === "system") {
      result.push(msg);
      fingerprints.push(0n); // Don't compare system messages
      continue;
    }

    // Skip very short messages — not worth deduplicating
    if (msg.text.length < MIN_MESSAGE_LENGTH || msg.tokenCount < MIN_TOKEN_COUNT) {
      result.push(msg);
      fingerprints.push(simhashFingerprint(msg.text));
      continue;
    }

    const fp = simhashFingerprint(msg.text);

    // Check against all previous fingerprints
    let duplicateOf = -1;
    let bestDistance = Infinity;

    for (let j = 0; j < fingerprints.length; j++) {
      if (fingerprints[j] === 0n) continue; // Skip system messages
      const dist = hammingDistance(fp, fingerprints[j]);
      if (dist <= threshold && dist < bestDistance) {
        bestDistance = dist;
        duplicateOf = j;
      }
    }

    if (duplicateOf >= 0) {
      // Found a near-duplicate — replace with a compact reference
      const originalTokens = msg.tokenCount;
      const savedTokens = originalTokens - 30; // Reference is ~30 tokens

      if (msg.isToolResult) {
        // Tool results can be replaced with a one-liner
        const reference = `[deduplicated: similar to message #${duplicateOf + 1}, saved ~${savedTokens} tokens]`;
        result.push(modifyToolResultContent(msg, reference));
      } else {
        // Non-tool messages: create a compact system reference
        const reference = `[deduplicated: similar to message #${duplicateOf + 1} (${msg.role}, ${originalTokens} tokens)]`;
        // Replace text content but keep the message structure
        result.push({
          ...msg,
          text: reference,
          tokenCount: estimateTextTokens(reference) + 4,
        });
      }

      fingerprints.push(fp); // Track even deduped messages for chain detection
      dedupedCount++;
      tokensSaved += Math.max(0, savedTokens);
      dedupPairs.push({ from: i, similarTo: duplicateOf, distance: bestDistance });
    } else {
      // Not a duplicate — keep as-is
      result.push(msg);
      fingerprints.push(fp);
    }
  }

  return {
    messages: result,
    dedupedCount,
    tokensSaved,
    dedupPairs,
  };
}