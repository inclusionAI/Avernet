/**
 * GRT knowledge base adapter for ClawFlow.
 *
 * Uses a configuration loaded from the knowledge_bases DB table to query
 * the GRT API (matching the format from grt_search.py).
 */
import type { KnowledgeBase, KnowledgeBaseSearchResult } from "./types.js";

/** Configuration for the GRT adapter — matches knowledge_bases DB row. */
export type GrtKbConfig = {
  kbId: string;
  name: string;
  instanceName: string;
  interfaceName: string;
  token: string;
  userName: string;
  userId: string;
  topK: number;
  rankingThreshold: number;
  vectorThreshold: number;
  rankingModel: string;
  env: string;
};

const DEFAULT_TOP_K = 3;
const DEFAULT_RANKING_THRESHOLD = 0.01;
const DEFAULT_VECTOR_THRESHOLD = 0.6;
const DEFAULT_RANKING_MODEL = "bge-reranker-base";

/** Truncate to maxChars, appending a marker if truncated. */
function truncate(text: string, maxChars: number): string {
  if (text.length <= maxChars) return text;
  return text.slice(0, maxChars - 12) + "\n..._truncated_";
}

/** Clamp a value to [min, max]. */
function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

/**
 * GRT knowledge base adapter.
 *
 * Queries the GRT vector search service and normalizes results.
 * The API endpoint and parameters are determined by the stored config.
 */
export class GrtKbAdapter implements KnowledgeBase {
  readonly type = "grt";
  readonly maxResults?: number;

  private readonly config: GrtKbConfig;
  private readonly maxContentChars: number;

  constructor(config: GrtKbConfig, maxResults?: number, maxContentChars = 2000) {
    this.config = config;
    this.maxResults = maxResults;
    this.maxContentChars = maxContentChars;
  }

  async search(query: string, maxResults: number): Promise<KnowledgeBaseSearchResult[]> {
    const limit = this.maxResults ?? maxResults;
    try {
      const baseUrl = this.config.env.toLowerCase() === "pre"
        ? (process.env.GRT_KB_PRE_URL || "")
        : (process.env.GRT_KB_PROD_URL || "");

      if (!baseUrl) {
        console.warn(
          `[taskguard] GRT KB adapter: base URL not configured ` +
          `(set GRT_KB_PRE_URL or GRT_KB_PROD_URL for env "${this.config.env}"); skipping KB search`,
        );
        return [];
      }

      const body = {
        instanceName: this.config.instanceName,
        token: this.config.token,
        interfaceName: this.config.interfaceName,
        userName: this.config.userName,
        userId: this.config.userId,
        env: this.config.env,
        param: {
          question: query,
          topK: String(this.config.topK || DEFAULT_TOP_K),
          rankingThreshold: String(this.config.rankingThreshold ?? DEFAULT_RANKING_THRESHOLD),
          rankingModel: this.config.rankingModel || DEFAULT_RANKING_MODEL,
          threshold: String(this.config.vectorThreshold ?? DEFAULT_VECTOR_THRESHOLD),
        },
      };

      const res = await fetch(baseUrl, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-webgw-appid": "kbsservice",
          "x-webgw-version": "2.0",
        },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(15_000),
      });

      if (!res.ok) return [];

      const json = (await res.json()) as GrtResponse;
      return this.parseResults(json, limit);
    } catch {
      return [];
    }
  }

  private parseResults(json: GrtResponse, limit: number): KnowledgeBaseSearchResult[] {
    const code = json.code ?? json.Code;
    if (code && String(code) !== "OK" && String(code) !== "200" && String(code) !== "0") {
      return [];
    }

    const runResult = (json.runResult ?? json) as Record<string, unknown>;
    const answerList = Array.isArray(runResult.answer) ? runResult.answer : [];

    const results: KnowledgeBaseSearchResult[] = [];
    for (const rawItem of answerList) {
      const item = typeof rawItem === "string" ? (tryParseJson(rawItem) ?? { a: rawItem }) : (rawItem as Record<string, unknown>);
      const content = truncate(String(item.a ?? item.content ?? item.text ?? item.answer ?? ""), this.maxContentChars);
      const score = clamp(Number(item.rerankScore ?? item.score ?? item.rankingScore ?? 0), 0, 1);
      const labels = (item.labels ?? {}) as Record<string, unknown>;
      const title = String(labels.title ?? item.title ?? item.q ?? `Result ${results.length + 1}`);
      const source = String(labels.source_description ?? labels.url ?? item.ref ?? this.config.instanceName);
      results.push({
        id: `grt-${this.config.kbId}-${results.length}`,
        title,
        content,
        source,
        relevance: score,
      });
    }

    results.sort((a, b) => b.relevance - a.relevance);
    return results.slice(0, limit);
  }
}

function tryParseJson(text: string): Record<string, unknown> | null {
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    return null;
  }
}

type GrtResponse = {
  code?: string | number;
  Code?: string | number;
  message?: string;
  errorMsg?: string;
  runResult?: { answer?: Array<string | Record<string, unknown>> };
  answer?: Array<string | Record<string, unknown>>;
};