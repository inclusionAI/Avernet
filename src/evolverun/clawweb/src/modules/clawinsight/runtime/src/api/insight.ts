import { fetchJson } from "./client";
import type {
  InsightOverview,
  InsightScopeParams,
  InsightTrend,
  FailureTaskDetail,
  FailureTaskPage,
  ImprovementDetail,
  ImprovementHandoff,
  ImprovementPage,
  AdminImprovementPage,
  AdminExecuteOnceResult,
  ImprovementView,
  AutoRepairGrantView,
  TimelinePage,
} from "../types/insight";

const BASE = "/api/insight/v1";

function queryString(params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    if (Array.isArray(value)) {
      value.forEach((item) => search.append(key, String(item)));
      return;
    }
    search.set(key, String(value));
  });
  const result = search.toString();
  return result ? `?${result}` : "";
}

function taskPath(sessionId: string, taskIndex: number): string {
  return `${BASE}/failure-tasks/${encodeURIComponent(sessionId)}/tasks/${taskIndex}`;
}

export const insightApi = {
  overview(params: InsightScopeParams = {}): Promise<InsightOverview> {
    return fetchJson(`${BASE}/overview${queryString(params)}`);
  },

  trend(params: InsightScopeParams = {}): Promise<InsightTrend> {
    return fetchJson(`${BASE}/trend${queryString(params)}`);
  },

  failureTasks(
    params: InsightScopeParams & {
      failureClass?: string;
      completionStates?: number[];
      cursor?: string;
      pageSize?: number;
    } = {},
  ): Promise<FailureTaskPage> {
    return fetchJson(
      `${BASE}/failure-tasks${queryString({
        ...params,
        completionStates: params.completionStates?.join(","),
      })}`,
    );
  },

  failureTaskDetail(
    sessionId: string,
    taskIndex: number,
    params: {
      anchorTaskIndex?: number;
      ownerUserId?: string;
    } = {},
  ): Promise<FailureTaskDetail> {
    return fetchJson(`${taskPath(sessionId, taskIndex)}${queryString(params)}`);
  },

  timeline(
    sessionId: string,
    taskIndex: number,
    params: {
      cursor?: string;
      blockId?: string;
      position?: "tail";
      all?: boolean;
      pageSize?: number;
      anchorTaskIndex?: number;
      ownerUserId?: string;
    } = {},
  ): Promise<TimelinePage> {
    return fetchJson(
      `${taskPath(sessionId, taskIndex)}/timeline${queryString(params)}`,
    );
  },

  createImprovement(
    input: {
      title: string;
      botId: string;
      selectedTasks: Array<{ sessionId: string; taskIndex: number }>;
      userGuidance?: string;
      ownerUserId?: string;
      sourceOwnerUserId?: string;
    },
    idempotencyKey: string,
  ): Promise<ImprovementDetail> {
    return fetchJson(`${BASE}/improvements`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(input),
    });
  },

  createImprovementsBatch(
    items: Array<{
      title: string;
      botId: string;
      selectedTasks: Array<{ sessionId: string; taskIndex: number }>;
      userGuidance?: string;
      ownerUserId?: string;
      sourceOwnerUserId?: string;
    }>,
    idempotencyKey: string,
  ): Promise<{ items: ImprovementDetail[] }> {
    return fetchJson(`${BASE}/improvements/batch`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ items }),
    });
  },

  improvements(
    params: {
      ownerUserId?: string;
      botId?: string;
      status?: string;
      cursor?: string;
      pageSize?: number;
    } = {},
  ): Promise<ImprovementPage> {
    return fetchJson(`${BASE}/improvements${queryString(params)}`);
  },

  improvement(improvementId: number): Promise<ImprovementDetail> {
    return fetchJson(
      `${BASE}/improvements/${encodeURIComponent(improvementId)}`,
    );
  },

  improvementHandoff(improvementId: number): Promise<ImprovementHandoff> {
    return fetchJson(
      `${BASE}/improvements/${encodeURIComponent(improvementId)}/handoff`,
    );
  },

  recordSelfRepairHandoff(
    improvementId: number,
    version: number,
  ): Promise<ImprovementView> {
    return fetchJson(
      `${BASE}/improvements/${encodeURIComponent(improvementId)}/self-repair-handoff`,
      {
        method: "POST",
        body: JSON.stringify({ version }),
      },
    );
  },

  updateImprovement(
    improvementId: number,
    input: {
      title?: string;
      userGuidance?: string | null;
      status?: "ACTIVE" | "IN_PROGRESS" | "RESOLVED" | "ARCHIVED";
      version?: number;
    },
  ): Promise<ImprovementView> {
    return fetchJson(
      `${BASE}/improvements/${encodeURIComponent(improvementId)}`,
      {
        method: "PATCH",
        body: JSON.stringify(input),
      },
    );
  },

  markHandled(improvementId: number, version: number): Promise<ImprovementView> {
    return fetchJson(`${BASE}/improvements/${encodeURIComponent(improvementId)}/handled`, {
      method: "POST",
      body: JSON.stringify({ version }),
    });
  },

  rejectImprovement(
    improvementId: number,
    input: { reasonCode: string; comment?: string; version: number },
  ): Promise<ImprovementView> {
    return fetchJson(`${BASE}/improvements/${encodeURIComponent(improvementId)}/reject`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  },

  adminImprovements(
    params: {
      ownerUserId?: string;
      botId?: string;
      status?: string;
      adminReviewStatus?: string;
      includeAll?: boolean;
      cursor?: string;
      pageSize?: number;
    } = {},
  ): Promise<AdminImprovementPage> {
    return fetchJson(`${BASE}/admin/improvements${queryString(params)}`);
  },

  adminImprovement(improvementId: number): Promise<ImprovementDetail> {
    return fetchJson(`${BASE}/admin/improvements/${encodeURIComponent(improvementId)}`);
  },

  reviewAdminImprovement(
    improvementId: number,
    input: { decision: "APPROVE" | "REJECT"; comment?: string; version: number },
  ): Promise<ImprovementView> {
    return fetchJson(`${BASE}/admin/improvements/${encodeURIComponent(improvementId)}/review`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  },

  adminExecuteOnce(
    improvementId: number,
    input: {
      reason: string;
      repairDirection?: string;
      targetUserId?: string;
      targetBotId?: string;
      botEnv?: string;
      crossBotConfirmed?: boolean;
      maxRounds?: number;
    },
    idempotencyKey: string,
  ): Promise<AdminExecuteOnceResult> {
    return fetchJson(`${BASE}/admin/improvements/${encodeURIComponent(improvementId)}/execute-once`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(input),
    });
  },

  adminMarkHandled(improvementId: number, version: number): Promise<ImprovementView> {
    return fetchJson(`${BASE}/admin/improvements/${encodeURIComponent(improvementId)}/handled`, {
      method: "POST",
      body: JSON.stringify({ version }),
    });
  },

  adminRejectImprovement(
    improvementId: number,
    input: { reasonCode: string; comment?: string; version: number },
  ): Promise<ImprovementView> {
    return fetchJson(`${BASE}/admin/improvements/${encodeURIComponent(improvementId)}/reject`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  },

  adminReopenImprovement(
    improvementId: number,
    input: { reason?: string; version: number },
  ): Promise<ImprovementView> {
    return fetchJson(`${BASE}/admin/improvements/${encodeURIComponent(improvementId)}/reopen`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  },

  adminConsentLink(improvementId: number): Promise<{ improvementId: number; ownerUserId: string; botId: string; expiresAt: string; url: string }> {
    return fetchJson(`${BASE}/admin/improvements/${encodeURIComponent(improvementId)}/consent-link`);
  },

  autoRepairGrants(): Promise<{ items: AutoRepairGrantView[] }> {
    return fetchJson(`${BASE}/auto-repair-grants`);
  },

  revokeAutoRepairGrant(grantId: number, version: number): Promise<AutoRepairGrantView> {
    return fetchJson(`${BASE}/auto-repair-grants/${encodeURIComponent(grantId)}`, {
      method: "DELETE",
      body: JSON.stringify({ version }),
    });
  },
};
