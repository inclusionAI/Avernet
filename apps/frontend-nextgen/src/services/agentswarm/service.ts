/**
 * Client for `/openapi/v1/agentswarm`.
 *
 * The gateway owns run truth: nothing here caches, advances or invents a run
 * state. Callers poll `getRun`/`listRuns` and read `getRunDetail` on demand.
 */
import { agentswarmRequest } from './http';
import type {
  CancelResponseDTO,
  DefinitionDTO,
  DefinitionListDTO,
  DefinitionWriteDTO,
  ListRunsQuery,
  QueryDefinitionsDTO,
  RunDetailDTO,
  RunListDTO,
  RunSummaryDTO,
  SubmitRunDTO,
  SubmitRunResponseDTO,
  UpdateDefinitionDTO,
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
export function validateIdentitySource(dto: Pick<SubmitRunDTO, 'agents' | 'agent_refs' | 'identity_bot_id'>): string | null {
  // A definition-ref roster is validated server-side against the stored kind;
  // without that knowledge the safer assumption is that Bots are involved,
  // because refusing a needing run is recoverable while a 400 mid-submit is not.
  const needsIdentity =
    (dto.agents ?? []).some((agent) => agent.kind === 'bot') || (dto.agent_refs ?? []).length > 0;
  if (!needsIdentity) return null;
  if ((dto.identity_bot_id || '').trim()) return null;
  return '请选择执行权限 Bot：包含 Bot 类型 Agent 的运行必须指定执行权限 Bot';
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

/**
 * The stored definition library: a per-owner, versioned set of reusable Agents.
 *
 * Reads and writes are POST-with-body, not REST verbs, because the gateway's
 * definition API is POST-only (the id travels in the body). Scoping is by the
 * authenticated caller server-side; nothing here filters by owner.
 */
const DEFINITIONS = '/agent/definitions';

export async function listDefinitions(
  tenant: string,
  query: QueryDefinitionsDTO = {},
): Promise<DefinitionListDTO> {
  return agentswarmRequest<DefinitionListDTO>(`${DEFINITIONS}/list`, {
    method: 'POST',
    params: tenantParams(tenant),
    data: { page: 1, page_size: 50, ...query },
  });
}

export async function getDefinition(tenant: string, definitionId: string): Promise<DefinitionDTO> {
  return agentswarmRequest<DefinitionDTO>(`${DEFINITIONS}/get`, {
    method: 'POST',
    params: tenantParams(tenant),
    data: { definition_id: definitionId },
  });
}

export async function createDefinition(
  tenant: string,
  body: DefinitionWriteDTO,
): Promise<DefinitionDTO> {
  return agentswarmRequest<DefinitionDTO>(`${DEFINITIONS}/add`, {
    method: 'POST',
    params: tenantParams(tenant),
    data: body,
  });
}

export async function updateDefinition(
  tenant: string,
  body: UpdateDefinitionDTO,
): Promise<DefinitionDTO> {
  return agentswarmRequest<DefinitionDTO>(`${DEFINITIONS}/update`, {
    method: 'POST',
    params: tenantParams(tenant),
    data: body,
  });
}

/** Retires every live version. History stays readable; the definition is gone from lists. */
export async function deleteDefinition(
  tenant: string,
  definitionId: string,
): Promise<{ retired: number }> {
  return agentswarmRequest<{ retired: number }>(`${DEFINITIONS}/delete`, {
    method: 'POST',
    params: tenantParams(tenant),
    data: { definition_id: definitionId },
  });
}