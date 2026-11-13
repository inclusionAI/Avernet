/**
 * Client for `/openapi/v1/agentswarm`.
 *
 * The gateway owns run truth: nothing here caches, advances or invents a run
 * state. Callers poll `getRun`/`listRuns` and read `getRunDetail` on demand.
 */
import { agentswarmRequest } from './http';
import type {
  CancelResponseDTO,
  ListRunsQuery,
  RunDetailDTO,
  RunListDTO,
  RunSummaryDTO,
  SubmitRunDTO,
  SubmitRunResponseDTO,
} from './types';

/**
 * Tenancy is a request parameter on this surface, not derived from the
 * principal, and must be supplied by the caller's authenticated scope.
 */
export function tenantParams(tenant: string) {
  const trimmed = (tenant || '').trim();
  if (!trimmed) throw new Error('当前登录身份缺少租户信息，无法发起计算任务');
  return { tenant: trimmed };
}

/**
 * A bot-kind agent runs under an identity source; `llm` agents run in-process
 * and need none, so a pure-llm run is exempt.
 */
export function requiresIdentitySource(agents: Array<{ kind?: string | null }>): boolean {
  return agents.some((agent) => agent.kind === 'bot');
}

/**
 * Mirror of the service-side refusal so the user sees it before a request is
 * made, instead of as a 400 after one.
 */
export function validateIdentitySource(dto: Pick<SubmitRunDTO, 'agents' | 'identity_bot_id'>): string | null {
  if (!requiresIdentitySource(dto.agents)) return null;
  if ((dto.identity_bot_id || '').trim()) return null;
  return '请选择身份 Bot：包含 Bot 类型 Agent 的运行必须指定身份 Bot';
}

export async function submitRun(tenant: string, dto: SubmitRunDTO): Promise<SubmitRunResponseDTO> {
  const refusal = validateIdentitySource(dto);
  if (refusal) throw new Error(refusal);
  const identity = (dto.identity_bot_id || '').trim();
  return agentswarmRequest<SubmitRunResponseDTO>('/runs', {
    method: 'POST',
    params: tenantParams(tenant),
    // Omit the field entirely when absent: an empty string is not a source.
    data: identity ? { ...dto, identity_bot_id: identity } : { ...dto, identity_bot_id: undefined },
  });
}

export async function cancelRun(tenant: string, runId: string): Promise<CancelResponseDTO> {
  return agentswarmRequest<CancelResponseDTO>(`/runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
    params: tenantParams(tenant),
  });
}

export async function getRun(tenant: string, runId: string): Promise<RunSummaryDTO> {
  return agentswarmRequest<RunSummaryDTO>(`/runs/${encodeURIComponent(runId)}`, {
    params: tenantParams(tenant),
  });
}

export async function listRuns(tenant: string, query: ListRunsQuery = {}): Promise<RunListDTO> {
  return agentswarmRequest<RunListDTO>('/runs', {
    params: { ...tenantParams(tenant), ...query },
  });
}

export async function getRunDetail(tenant: string, runId: string): Promise<RunDetailDTO> {
  return agentswarmRequest<RunDetailDTO>(`/runs/${encodeURIComponent(runId)}/detail`, {
    params: tenantParams(tenant),
  });
}