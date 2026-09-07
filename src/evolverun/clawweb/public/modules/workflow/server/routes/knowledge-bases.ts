/**
 * Knowledge Bases API routes — CRUD and test-search for GRT KB configurations,
 * plus YuQue (语雀) document search.
 */
import { Router, type Request, type Response } from "express";
import type { KnowledgeBaseRepository } from "../repositories/knowledge-base-repository.js";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

interface YuQueConfig {
  yuqueBookId: number | null;
  yuqueToken: string;
  yuqueApiBaseUrl: string;
  yuqueKnowledgeBases?: Array<{ bookId: number; name: string; token: string }>;
}

export function createKnowledgeBasesRouter(kbRepo: KnowledgeBaseRepository | null, yuqueConfig?: YuQueConfig): Router {
  const router = Router();

  /** GET / — list all KB configs, optional ?enabled=true filter */
  router.get("/", asyncHandler(async (req: Request, res: Response) => {
    if (!kbRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const enabledOnly = req.query.enabled === "true";
      const rows = await kbRepo.listAll(enabledOnly);
      res.json(rows.map(rowToApi));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /:kbId — get a single KB config */
  router.get("/:kbId", asyncHandler(async (req: Request, res: Response) => {
    if (!kbRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const row = await kbRepo.findByKbId(String(req.params.kbId));
      if (!row) {
        res.status(404).json({ error: "Not Found", message: `Knowledge base "${req.params.kbId}" not found` });
        return;
      }
      res.json(rowToApi(row));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** POST / — create a new KB config */
  router.post("/", asyncHandler(async (req: Request, res: Response) => {
    if (!kbRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { kb_id, name, instance_name, interface_name, token, description, user_name, user_id, top_k, ranking_threshold, vector_threshold, ranking_model, env } = req.body;

      if (!kb_id || !name || !instance_name || !interface_name || !token) {
        res.status(400).json({ error: "Bad Request", message: "Missing required fields: kb_id, name, instance_name, interface_name, token" });
        return;
      }

      const existing = await kbRepo.findByKbId(kb_id);
      if (existing) {
        res.status(409).json({ error: "Conflict", message: `Knowledge base "${kb_id}" already exists` });
        return;
      }

      const row = await kbRepo.create({
        kb_id,
        name,
        instance_name,
        interface_name,
        token,
        description,
        user_name,
        user_id,
        top_k,
        ranking_threshold,
        vector_threshold,
        ranking_model,
        env,
      });
      res.status(201).json(rowToApi(row));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:kbId — update a KB config */
  router.put("/:kbId", asyncHandler(async (req: Request, res: Response) => {
    if (!kbRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { name, description, instance_name, interface_name, token, user_name, user_id, top_k, ranking_threshold, vector_threshold, ranking_model, env, enabled } = req.body;

      const row = await kbRepo.update(String(req.params.kbId), {
        name,
        description,
        instance_name,
        interface_name,
        token,
        user_name,
        user_id,
        top_k,
        ranking_threshold,
        vector_threshold,
        ranking_model,
        env,
        enabled,
      });

      if (!row) {
        res.status(404).json({ error: "Not Found", message: `Knowledge base "${req.params.kbId}" not found` });
        return;
      }
      res.json(rowToApi(row));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** DELETE /:kbId — delete a KB config */
  router.delete("/:kbId", asyncHandler(async (req: Request, res: Response) => {
    if (!kbRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const deleted = await kbRepo.delete(String(req.params.kbId));
      if (!deleted) {
        res.status(404).json({ error: "Not Found", message: `Knowledge base "${req.params.kbId}" not found` });
        return;
      }
      res.json({ affected: true });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** POST /:kbId/test — test a KB config by performing a GRT search */
  router.post("/:kbId/test", asyncHandler(async (req: Request, res: Response) => {
    if (!kbRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { query } = req.body as { query?: string };
      if (!query || typeof query !== "string") {
        res.status(400).json({ error: "Bad Request", message: "Missing required field: query" });
        return;
      }

      const kb = await kbRepo.findByKbId(String(req.params.kbId));
      if (!kb) {
        res.status(404).json({ error: "Not Found", message: `Knowledge base "${req.params.kbId}" not found` });
        return;
      }

      if (!kb.enabled) {
        res.status(400).json({ error: "Bad Request", message: `Knowledge base "${req.params.kbId}" is disabled` });
        return;
      }

      const searchResult = await grtSearch({
        question: query,
        instanceName: kb.instance_name,
        interfaceName: kb.interface_name,
        token: kb.token,
        userName: kb.user_name,
        userId: kb.user_id,
        topK: kb.top_k,
        rankingThreshold: kb.ranking_threshold,
        vectorThreshold: kb.vector_threshold,
        rankingModel: kb.ranking_model,
        env: kb.env,
      });

      res.json(searchResult);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(502).json({ error: "Bad Gateway", message: `GRT search failed: ${msg}` });
    }
  }));

  /** POST /yuque/search — search YuQue documents via OpenAPI
   *  Supports single bookId or multiple bookIds (array).
   *  When bookIds is provided, searches each book in parallel using per-book tokens
   *  from yuqueKnowledgeBases config, and merges results with kbName tags.
   */
  router.post("/yuque/search", asyncHandler(async (req: Request, res: Response) => {
    try {
      const { query, bookId, bookIds } = req.body as { query?: string; bookId?: number; bookIds?: number[] };
      if (!query || typeof query !== "string") {
        res.status(400).json({ error: "Bad Request", message: "Missing required field: query" });
        return;
      }

      const apiBaseUrl = (yuqueConfig?.yuqueApiBaseUrl ?? "https://yuque.antfin.com").replace(/\/+$/, "");
      const configuredKbs = yuqueConfig?.yuqueKnowledgeBases ?? [];

      // Build a lookup: bookId → { name, token }
      const kbMap = new Map<number, { name: string; token: string }>();
      for (const kb of configuredKbs) {
        kbMap.set(kb.bookId, { name: kb.name, token: kb.token });
      }

      // Determine which books to search
      const idsToSearch = bookIds && bookIds.length > 0
        ? bookIds
        : bookId
          ? [bookId]
          : yuqueConfig?.yuqueBookId
            ? [yuqueConfig.yuqueBookId]
            : [];

      if (idsToSearch.length > 0 && configuredKbs.length > 0) {
        // Search each configured book in parallel using per-book tokens
        const results = await Promise.all(
          idsToSearch.map((bid) => {
            const kbInfo = kbMap.get(bid);
            if (!kbInfo) return Promise.resolve({ query, items: [], total: 0, kbName: "" });
            return yuqueSearch({ query, token: kbInfo.token, apiBaseUrl, bookId: bid })
              .then((r) => ({ ...r, kbName: kbInfo.name }))
              .catch(() => ({ query, items: [], total: 0, kbName: kbInfo.name }));
          })
        );
        // Tag each item with its kbName
        const merged = results.flatMap((r) =>
          r.items.map((item) => ({ ...item, kbName: r.kbName }))
        );
        merged.sort((a, b) => b.score - a.score);
        res.json({ query, items: merged, total: merged.length });
      } else if (yuqueConfig?.yuqueToken) {
        // Legacy: single global token, no per-book config
        const searchResult = await yuqueSearch({ query, token: yuqueConfig.yuqueToken, apiBaseUrl, bookId: bookId ?? yuqueConfig.yuqueBookId ?? undefined });
        res.json(searchResult);
      } else {
        res.status(503).json({ error: "Service Unavailable", message: "YuQue token not configured" });
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(502).json({ error: "Bad Gateway", message: `YuQue search failed: ${msg}` });
    }
  }));

  /** GET /yuque/books — list configured YuQue knowledge bases (no tokens exposed) */
  router.get("/yuque/books", asyncHandler(async (_req: Request, res: Response) => {
    const configuredKbs = yuqueConfig?.yuqueKnowledgeBases ?? [];
    res.json(configuredKbs.map((kb) => ({
      bookId: kb.bookId,
      name: kb.name,
    })));
  }));

  return router;
}

/** Convert DB row (snake_case) to API response (camelCase) */
function rowToApi(row: {
  id: number;
  kb_id: string;
  name: string;
  description: string | null;
  instance_name: string;
  interface_name: string;
  token: string;
  user_name: string;
  user_id: string;
  top_k: number;
  ranking_threshold: number;
  vector_threshold: number;
  ranking_model: string;
  env: string;
  enabled: number;
  gmt_create: number;
  gmt_modified: number;
}) {
  return {
    id: row.id,
    kbId: row.kb_id,
    name: row.name,
    description: row.description,
    instanceName: row.instance_name,
    interfaceName: row.interface_name,
    token: row.token,
    userName: row.user_name,
    userId: row.user_id,
    topK: row.top_k,
    rankingThreshold: row.ranking_threshold,
    vectorThreshold: row.vector_threshold,
    rankingModel: row.ranking_model,
    env: row.env,
    enabled: row.enabled === 1,
    gmtCreate: row.gmt_create,
    gmtModified: row.gmt_modified,
  };
}

/** GRT search — matches the request format from grt_search.py */
async function grtSearch(params: {
  question: string;
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
}): Promise<{ query: string; items: Array<{ content: string; score: number; title: string; source: string }>; total: number }> {
  const url = params.env.toLowerCase() === "pre"
    ? "https://webgw-pre.alipay.com/smartinfrafaas/com.alipay.sofa.function.SOFAFunction/apply/myjf.common.smartinfrafaas.trwrapper.knowledgebase.runservice"
    : "https://webgw.alipay.com/smartinfrafaas/com.alipay.sofa.function.SOFAFunction/apply/myjf.common.smartinfrafaas.trwrapper.knowledgebase.runservice";

  const requestBody = {
    instanceName: params.instanceName,
    token: params.token,
    interfaceName: params.interfaceName,
    userName: params.userName,
    userId: params.userId,
    env: params.env,
    param: {
      question: params.question,
      topK: String(params.topK),
      rankingThreshold: String(params.rankingThreshold),
      rankingModel: params.rankingModel,
      threshold: String(params.vectorThreshold),
    },
  };

  const headers: Record<string, string> = {
    "content-type": "application/json",
    "x-webgw-appid": "kbsservice",
    "x-webgw-version": "2.0",
  };

  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(requestBody),
    signal: AbortSignal.timeout(30000),
  });

  if (!response.ok) {
    throw new Error(`GRT API returned ${response.status}: ${await response.text().catch(() => "unknown error")}`);
  }

  const data = await response.json() as Record<string, unknown>;
  const code = data.code ?? data.code;
  if (code && String(code) !== "OK" && String(code) !== "200" && String(code) !== "0") {
    throw new Error(`GRT API error: code=${code}, message=${data.message ?? data.errorMsg ?? "unknown"}`);
  }

  const runResult = (data.runResult ?? data) as Record<string, unknown>;
  const answerList = Array.isArray(runResult.answer) ? runResult.answer : [];

  const items: Array<{ content: string; score: number; title: string; source: string }> = [];
  for (const rawItem of answerList) {
    const item = typeof rawItem === "string" ? JSON.parse(rawItem) as Record<string, unknown> : rawItem as Record<string, unknown>;
    const content = String(item.a ?? item.content ?? item.text ?? item.answer ?? "");
    const score = Number(item.rerankScore ?? item.score ?? item.rankingScore ?? 0);
    const labels = (item.labels ?? {}) as Record<string, unknown>;
    const title = String(labels.title ?? item.title ?? item.q ?? "");
    const source = String(labels.source_description ?? labels.url ?? item.ref ?? "");
    items.push({ content, score, title, source });
  }

  items.sort((a, b) => b.score - a.score);

  return { query: params.question, items, total: items.length };
}

/** YuQue search via OpenAPI v2 — searches documents and fetches details */
async function yuqueSearch(params: {
  query: string;
  token: string;
  apiBaseUrl: string;
  bookId?: number;
  maxResults?: number;
}): Promise<{ query: string; items: Array<{ content: string; score: number; title: string; source: string }>; total: number }> {
  const limit = params.maxResults ?? 10;
  // YuQue v2 search: scope requires namespace (e.g. "zeodup/vh3397"), not numeric bookId.
  // We do a global search with the token and post-filter by bookId instead.
  const url = `${params.apiBaseUrl}/api/v2/search?q=${encodeURIComponent(params.query)}&type=doc&limit=${limit}`;

  const response = await fetch(url, {
    headers: { "X-Auth-Token": params.token },
    signal: AbortSignal.timeout(15000),
  });

  if (!response.ok) {
    throw new Error(`YuQue API returned ${response.status}: ${await response.text().catch(() => "unknown error")}`);
  }

  const json = await response.json() as {
    data?: Array<{
      id?: number;
      title?: string;
      slug?: string;
      book_id?: number;
      body?: string;
      highlighted?: { body?: string };
    }>;
  };

  const hits = json.data ?? [];

  // Post-filter by bookId when specified (scope=numericId is not supported by YuQue API)
  const filtered = params.bookId
    ? hits.filter((h) => String(h.book_id) === String(params.bookId))
    : hits;

  const capped = filtered.slice(0, limit);

  const items: Array<{ content: string; score: number; title: string; source: string }> = [];

  // Fetch doc details for richer content
  await Promise.all(
    capped.map(async (hit) => {
      try {
        let content = hit.highlighted?.body ?? hit.body ?? "";

        // Try to fetch full doc detail if we have bookId and slug
        if (hit.slug && hit.book_id) {
          const detailUrl = `${params.apiBaseUrl}/api/v2/repos/${hit.book_id}/docs/${hit.slug}`;
          const detailRes = await fetch(detailUrl, {
            headers: { "X-Auth-Token": params.token },
            signal: AbortSignal.timeout(10000),
          });
          if (detailRes.ok) {
            const detailJson = await detailRes.json() as {
              data?: { body?: string; title?: string; word_count?: number };
            };
            if (detailJson.data?.body) {
              content = detailJson.data.body;
            }
          }
        }

        // Strip markdown for cleaner display
        const plainContent = stripMarkdown(content);
        const truncated = plainContent.length > 2000 ? plainContent.slice(0, 1990) + "\n..._truncated_" : plainContent;

        items.push({
          content: truncated,
          score: 1.0, // YuQue search API doesn't return relevance scores
          title: hit.title ?? "",
          source: hit.slug && hit.book_id
            ? `${params.apiBaseUrl}/repos/${hit.book_id}/docs/${hit.slug}`
            : "",
        });
      } catch {
        // Skip this hit if detail fetch fails
      }
    }),
  );

  return { query: params.query, items, total: items.length };
}

/** Strip common markdown markup for plain-text display */
function stripMarkdown(md: string): string {
  return md
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/(\*{1,3}|_{1,3})(.*?)\1/g, "$2")
    .replace(/`{1,3}[^`]*`{1,3}/g, "")
    .replace(/^>\s+/gm, "")
    .replace(/^[-*+]\s+/gm, "")
    .replace(/^\d+\.\s+/gm, "")
    .replace(/---+/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}