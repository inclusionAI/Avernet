import type {
  BenchCase,
  BenchRun,
  BenchSession,
  Domain,
  EvolutionVersion,
  JsonObject,
  ModelOptions,
  SpaceBot,
  Task,
  TaskPage,
  Template,
} from '@/domain/evolution/types';
import { backendRequest, type BackendRequestOptions } from '../httpClient';

export const EVOLVE_BASE = '/openapi/v1/clawevolve/teamclaw';
export const BENCH_BASE = '/openapi/v1/clawevolve/bench/teamclaw';
export function requestEvolution<T>(url: string, options: BackendRequestOptions = {}): Promise<T> {
  return backendRequest<T>(url, options);
}

const id = encodeURIComponent;
const domainPath = (owner: string, domain: string) => `${BENCH_BASE}/domains/${id(owner)}/${id(domain)}`;
const templatePath = (owner: string, domain: string, name: string) =>
  `${domainPath(owner, domain)}/templates/${id(name)}`;
// Node responses are raw objects/arrays, unlike Bot OpenAPI envelopes.
export function createEvolutionApi(spaceRequest: typeof requestEvolution) {
  return {
    logArchives: (taskId: string) =>
      spaceRequest<{ items: JsonObject[] }>(`${EVOLVE_BASE}/tasks/${id(taskId)}/log-archives`),
    requestLogArchive: (taskId: string) =>
      spaceRequest<JsonObject>(`${EVOLVE_BASE}/tasks/${id(taskId)}/log-archives`, {
        method: 'POST',
        data: {},
        retryOnTransient: false,
      }),
    logDownload: (taskId: string, archiveId: string) =>
      spaceRequest<{ url: string }>(`${EVOLVE_BASE}/tasks/${id(taskId)}/log-archives/${id(archiveId)}/download-url`),
    bots: (page = 1) =>
      spaceRequest<{ items: SpaceBot[]; total: number; page: number; pageSize: number }>(`${EVOLVE_BASE}/bots`, {
        params: { page, pageSize: 50, engine: 'openclaw' },
      }),
    versions: (taskId?: string) =>
      spaceRequest<{ items: EvolutionVersion[] }>(`${EVOLVE_BASE}/versions`, { params: { taskId } }),
    packs: () => spaceRequest<{ items: JsonObject[] }>(`${EVOLVE_BASE}/packs`),
    packDownload: (taskId: string, stepId: string, sourceKind: string, round?: number | null) =>
      spaceRequest<JsonObject>(`${EVOLVE_BASE}/tasks/${id(taskId)}/steps/${id(stepId)}/pack-download-url`, {
        params: { sourceKind, sourceRound: round },
      }),
    models: (signal?: AbortSignal) => spaceRequest<ModelOptions>(`${EVOLVE_BASE}/model-options`, { signal }),
    tasks: (
      params: {
        page?: number;
        pageSize?: number;
        scope?: string;
        query?: string;
        category?: string;
        taskTypes?: string;
        targetType?: 'bot';
        status?: string;
      } = {},
      signal?: AbortSignal,
    ) => spaceRequest<TaskPage>(`${EVOLVE_BASE}/tasks`, { params, signal }),
    task: (taskId: string, signal?: AbortSignal) =>
      spaceRequest<Task>(`${EVOLVE_BASE}/tasks/${id(taskId)}`, { signal }),
    create: (endpoint: string, data: JsonObject) =>
      spaceRequest<Task>(`${EVOLVE_BASE}/${endpoint}`, { method: 'POST', data, retryOnTransient: false }),
    stepAction: (taskId: string, stepId: string, action: 'retry' | 'cancel') =>
      spaceRequest<{ step: unknown }>(`${EVOLVE_BASE}/tasks/${id(taskId)}/steps/${id(stepId)}/${action}`, {
        method: 'POST',
        data: {},
        retryOnTransient: false,
      }),
    diff: (taskId: string, stepId: string) =>
      spaceRequest<string>(`${EVOLVE_BASE}/tasks/${id(taskId)}/steps/${id(stepId)}/diff`, { responseType: 'text' }),
    domains: (signal?: AbortSignal) => spaceRequest<Domain[]>(`${BENCH_BASE}/domains`, { signal }),
    domain: (owner: string, domain: string) => spaceRequest<Domain>(domainPath(owner, domain)),
    createDomain: (data: { domainId: string; name: string; description?: string }) =>
      spaceRequest<Domain>(`${BENCH_BASE}/domains`, { method: 'POST', data, retryOnTransient: false }),
    updateDomain: (owner: string, domain: string, data: { name: string; description: string }) =>
      spaceRequest<Domain>(domainPath(owner, domain), { method: 'PUT', data }),
    archiveDomain: (owner: string, domain: string) =>
      spaceRequest<Domain>(domainPath(owner, domain), { method: 'DELETE', retryOnTransient: false }),
    templates: (owner: string, domain: string) => spaceRequest<Template[]>(`${domainPath(owner, domain)}/templates`),
    template: (owner: string, domain: string, name: string) =>
      spaceRequest<Template>(templatePath(owner, domain, name)),
    saveTemplate: (owner: string, domain: string, name: string, data: { contentMd: string; displayName: string }) =>
      spaceRequest<Template>(templatePath(owner, domain, name), { method: 'PUT', data }),
    archiveTemplate: (owner: string, domain: string, name: string) =>
      spaceRequest<Template>(templatePath(owner, domain, name), { method: 'DELETE', retryOnTransient: false }),
    publish: (owner: string, domain: string, templates: { templateName: string; version: number }[]) =>
      spaceRequest<{ items: { templateName: string; success: boolean; reason: string }[] }>(
        `${domainPath(owner, domain)}/templates/batch-publish`,
        { method: 'POST', data: { templates }, retryOnTransient: false },
      ),
    upload: (owner: string, domain: string, files: File[]) => {
      const data = new FormData();
      files.forEach((file) => data.append('files', file));
      return spaceRequest<{ summary: JsonObject; items: { templateName: string; action: string; reason: string }[] }>(
        `${domainPath(owner, domain)}/uploads/scan`,
        { method: 'POST', rawBody: data, retryOnTransient: false },
      );
    },
    runs: (owner: string, domain: string, offset = 0) =>
      spaceRequest<{ runs: BenchRun[]; total: number }>(`${BENCH_BASE}/runs`, {
        params: { ownerUserId: owner, domainId: domain, limit: 20, offset },
      }),
    run: (runId: string) => spaceRequest<BenchRun>(`${BENCH_BASE}/runs/${id(runId)}`),
    results: (runId: string) => spaceRequest<BenchCase[]>(`${BENCH_BASE}/runs/${id(runId)}/results`),
    sessions: (runId: string) => spaceRequest<{ sessions: BenchSession[] }>(`${BENCH_BASE}/runs/${id(runId)}/sessions`),
    session: (runId: string, artifactId: string) =>
      spaceRequest<JsonObject>(`${BENCH_BASE}/runs/${id(runId)}/sessions/artifacts/${id(artifactId)}`),
    artifacts: (runId: string) => spaceRequest<JsonObject[]>(`${BENCH_BASE}/runs/${id(runId)}/artifacts`),
  };
}
