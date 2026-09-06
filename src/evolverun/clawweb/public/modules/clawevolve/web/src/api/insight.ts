import { fetchJson } from "./client";
import type {
  ImprovementDetail,
  ImprovementHandoff,
  ImprovementPage,
} from "../types/insight";

const BASE = "/api/insight/v1";

function queryString(params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    search.set(key, String(value));
  });
  const result = search.toString();
  return result ? `?${result}` : "";
}

/** Minimal ClawInsight HTTP contract consumed by Clawevolve task shells. */
export const insightApi = {
  improvements(
    params: { ownerUserId?: string; botId?: string; status?: string; cursor?: string; pageSize?: number } = {},
  ): Promise<ImprovementPage> {
    return fetchJson(`${BASE}/improvements${queryString(params)}`);
  },

  improvement(improvementId: number): Promise<ImprovementDetail> {
    return fetchJson(`${BASE}/improvements/${encodeURIComponent(improvementId)}`);
  },

  improvementHandoff(improvementId: number): Promise<ImprovementHandoff> {
    return fetchJson(`${BASE}/improvements/${encodeURIComponent(improvementId)}/handoff`);
  },
};
