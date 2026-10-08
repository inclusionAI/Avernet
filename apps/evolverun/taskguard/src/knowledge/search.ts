/**
 * Concurrent search across multiple knowledge bases with deduplication and caching.
 *
 * Adapted from ClawMind's knowledge/base.ts for ClawFlow.
 */
import type { KnowledgeBase, KnowledgeBaseSearchResult, CacheEntry } from "./types.js";

/** Default cache TTL in milliseconds. */
const DEFAULT_CACHE_TTL_MS = 60_000;

/**
 * Search all knowledge bases concurrently, deduplicate by ID, and sort by relevance.
 *
 * Each KB search is wrapped in a try/catch so one failure doesn't block others.
 * If a cache is provided and has a fresh entry, returns cached results immediately.
 */
export async function searchAllKnowledgeBases(
  knowledgeBases: KnowledgeBase[],
  query: string,
  maxResultsPerKb: number,
  cache?: Map<string, CacheEntry>,
  cacheTtlMs: number = DEFAULT_CACHE_TTL_MS,
): Promise<KnowledgeBaseSearchResult[]> {
  const cacheKey = `${query.trim().toLowerCase()}::${maxResultsPerKb}`;

  // Check cache
  if (cache) {
    const cached = cache.get(cacheKey);
    if (cached && Date.now() - cached.timestamp < cacheTtlMs) {
      return cached.results;
    }
  }

  // Search all KBs concurrently
  const allResults = await Promise.all(
    knowledgeBases.map(async (kb) => {
      try {
        return await kb.search(query, maxResultsPerKb);
      } catch {
        return [] as KnowledgeBaseSearchResult[];
      }
    }),
  );

  // Merge and deduplicate by ID
  const seen = new Set<string>();
  const merged: KnowledgeBaseSearchResult[] = [];
  for (const results of allResults) {
    for (const result of results) {
      if (!seen.has(result.id)) {
        seen.add(result.id);
        merged.push(result);
      }
    }
  }

  // Sort by relevance descending
  merged.sort((a, b) => b.relevance - a.relevance);

  // Store in cache
  if (cache) {
    cache.set(cacheKey, { results: merged, timestamp: Date.now() });
  }

  return merged;
}

/** Create a new search cache (Map<string, CacheEntry>). */
export function createSearchCache(): Map<string, CacheEntry> {
  return new Map();
}