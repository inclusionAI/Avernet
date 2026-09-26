import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage, BackendUnknownRecord } from '../types';
import { userScopedParams } from './botController';
import type { ChannelDto } from './botEditorChannelDtos';

export interface SkillDto extends BackendUnknownRecord {
  skill_id: string;
  name: string;
  description?: string;
  active: boolean;
  version?: string;
}
export interface CatalogSkillDto extends BackendUnknownRecord {
  id?: string | number;
  skill_id?: string;
  name: string;
  description?: string;
  version?: string;
  latest_published_version?: string;
}
export interface SpaceSkillDto extends BackendUnknownRecord {
  skill_id: string;
  name: string;
  description?: string;
  status?: string;
  latest_published_version?: { version?: number; name?: string };
}
export interface SkillCenterSkillDto extends BackendUnknownRecord {
  skillCode?: string;
  skillName?: string;
  description?: string;
  latestVersionNumber?: string | number;
  homepageUrl?: string;
}
export interface SkillCenterReferenceDto extends BackendUnknownRecord {
  reference_id: string;
  status:
    | 'QUEUED'
    | 'RESOLVING_VERSION'
    | 'MATERIALIZING'
    | 'ADDING_TO_SKILL_SET'
    | 'PROJECTING_RUNTIME'
    | 'COMPLETED'
    | 'FAILED';
  error_message?: string | null;
}
export interface ResourceDto extends BackendUnknownRecord {
  path: string;
  name: string;
  type: 'file' | 'folder';
  size?: number | null;
}
export interface RoutineDto extends BackendUnknownRecord {
  routine_id: string;
  bot_id: string;
  name: string;
  trigger: { type: 'schedule'; cron: string };
  command: string;
  enabled: boolean;
  timezone?: string;
  gmt_modified?: string;
}
export interface RoutineWrite {
  name: string;
  trigger: { type: 'schedule'; cron: string };
  command: string;
  enabled: boolean;
  timezone?: string;
}
export interface EngineStatusDto extends BackendUnknownRecord {
  engine: string;
  active_connections: number;
  running: boolean;
}
export interface ApprovalConfigDto extends BackendUnknownRecord {
  should_approval: boolean;
}
export interface BotMcpDto extends BackendUnknownRecord {
  server_code: string;
  active: boolean;
}
export interface SkillSetDto extends BackendUnknownRecord {
  id: string;
  name: string;
  description?: string;
  is_default: boolean;
  is_active: boolean;
}
export interface SkillSetResourceDto extends SkillSetDto {
  mcps: Array<{ server_code: string; name?: string; description?: string }>;
  clis: Array<{
    cli_code?: string;
    resource_code?: string;
    code?: string;
    name?: string;
    description?: string;
  }>;
}
export interface McpServerDto extends BackendUnknownRecord {
  server_code: string;
  name: string;
  description?: string;
}
export interface McpPermissionDto extends BackendUnknownRecord {
  has_access: boolean;
  access_level?: string | null;
  tool_permissions: Record<string, unknown>;
}
export interface RenderScreenDto extends BackendUnknownRecord {
  id: number;
  name: string;
  cdn_url: string;
  creator_id: string;
  created_at?: string;
  updated_at?: string;
}
export interface ServicePublicationDto extends BackendUnknownRecord {
  publication_id: number;
  card_id: string;
  version: number;
  status: string;
  internal_status: string;
  live_version?: number;
  available_actions: string[];
  deployment?: BackendUnknownRecord;
  approval?: BackendUnknownRecord;
  created_at: string;
  updated_at: string;
}
export interface EditLockDto extends BackendUnknownRecord {
  locked: boolean;
  acquired?: boolean | null;
  holder_user_id?: string | null;
  holder_name?: string | null;
  locked_at?: string | null;
  acquired_at?: string | null;
  created_at?: string | null;
  has_collaborators: boolean;
  is_owner_holder: boolean;
  need_lock: boolean;
}
export interface RoutineRunDto extends BackendUnknownRecord {
  run_id: string;
  routine_id: string;
  status: string;
  started_at?: string;
  finished_at?: string;
}
export interface IdentityFileInfoDto {
  type: string;
  exists: boolean;
  file_path: string;
}
export interface IdentityFileDto {
  type: string;
  bot_id: string;
  content: string;
  file_path: string;
}
const path = (botId: string, group: string) => `/openapi/v1/bots/${botId}/${group}`;
const request = <T>(url: string, method = 'GET', params: BackendUnknownRecord = {}, data?: unknown) =>
  backendRequest<BackendApiEnvelope<T>>(url, { method, params: userScopedParams(params), data });

export const botEditorController = {
  listSkills: (
    botId: string,
    options: { source?: 'LOCAL'; owner_id?: string; user_id?: string; page?: number; page_size?: number } = {},
  ) => request<BackendApiPage<SkillDto>>(path(botId, 'skills'), 'GET', { page: 1, page_size: 100, ...options }),
  setSkillActive: (botId: string, skillId: string, active: boolean) =>
    request(path(botId, `skills/${skillId}/${active ? 'activate' : 'deactivate'}`), 'POST'),
  deleteSkill: (botId: string, skillId: string) => request(path(botId, `skills/${skillId}`), 'DELETE'),
  uploadSkill: (botId: string, packageBody: ArrayBuffer) =>
    backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(path(botId, 'skills'), {
      method: 'POST',
      params: userScopedParams(),
      rawBody: packageBody,
      headers: { 'Content-Type': 'application/zip' },
    }),
  uploadSkillFolder: (botId: string, files: File[]) => {
    const body = new FormData();
    const paths = files.map((file) => (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name);
    files.forEach((file) => body.append('files', file, file.name));
    body.append('file_paths', JSON.stringify(paths));
    return backendRequest<BackendApiEnvelope<{ skill: SkillDto; operation: string }>>(
      path(botId, 'skills/upload-folder'),
      { method: 'POST', params: userScopedParams(), rawBody: body },
    );
  },
  listBotMcps: (botId: string) => request<BotMcpDto[]>(path(botId, 'mcps')),
  listMcpServers: () =>
    request<BackendApiPage<McpServerDto>>('/openapi/v1/bots/mcp/servers', 'GET', { page: 1, page_size: 100 }),
  searchMarketMcpServers: (params: { keyword?: string; tenants?: string[] } = {}) =>
    request<BackendApiPage<McpServerDto>>(
      '/openapi/v1/bots/market/mcp-servers',
      'POST',
      {},
      {
        page_num: 1,
        page_size: 100,
        ...params,
      },
    ),
  getMcpPermission: (serverCode: string) =>
    request<McpPermissionDto>(`/openapi/v1/bots/mcp/servers/${encodeURIComponent(serverCode)}/permissions`),
  listRepositorySkills: () =>
    request<BackendApiPage<CatalogSkillDto>>('/openapi/v1/bots/skills/repository', 'GET', {
      page: 1,
      page_size: 100,
      sort: 'latest',
    }),
  listSkillCenterSkills: (keyword = '', pageNum = 1, pageSize = 20) =>
    request<BackendApiPage<SkillCenterSkillDto>>(
      '/openapi/v1/bots/market/skill-center/skills',
      'POST',
      {},
      {
        keyword,
        pageNum,
        pageSize,
        sortBy: 'latest',
        tagList: [],
        creatorName: null,
        creatorWorkNo: null,
        belongTo: null,
        isOfficial: null,
        isRecommended: null,
      },
    ),
  listConsumableSpaceSkills: (spaceId: string, page = 1, pageSize = 100) =>
    request<BackendApiPage<SpaceSkillDto>>(
      `/openapi/v1/bots/spaces/${encodeURIComponent(spaceId)}/skills/consumable`,
      'GET',
      {
        page,
        page_size: pageSize,
      },
    ),
  createSkillCenterReferences: (botId: string, setId: string, skillCodes: string[]) =>
    backendRequest<BackendApiEnvelope<{ request_id: string; reference_ids: string[] }>>(
      path(botId, `skill-sets/${setId}/skill-center-references`),
      {
        method: 'POST',
        params: userScopedParams(),
        data: { skill_codes: skillCodes },
        headers: { 'Idempotency-Key': globalThis.crypto?.randomUUID?.() ?? `${botId}-${setId}-${Date.now()}` },
      },
    ),
  listSkillCenterReferences: (botId: string, setId: string, requestId: string) =>
    request<BackendApiPage<SkillCenterReferenceDto>>(
      path(botId, `skill-sets/${setId}/skill-center-references`),
      'GET',
      { request_id: requestId, page: 1, page_size: 20 },
    ),
  setMcpActive: (botId: string, serverCode: string, active: boolean) =>
    request<BotMcpDto>(
      path(botId, `mcps/${encodeURIComponent(serverCode)}/${active ? 'activate' : 'deactivate'}`),
      'POST',
    ),
  listSkillSets: (botId: string) => request<SkillSetDto[]>(path(botId, 'skill-sets')),
  listSkillSetResources: (botId: string, ownerId?: string) =>
    request<SkillSetResourceDto[]>(path(botId, 'skill-sets/resources'), 'GET', { owner_id: ownerId }),
  createSkillSet: (botId: string, body: { name: string; description?: string }) =>
    backendRequest<BackendApiEnvelope<SkillSetDto>>(path(botId, 'skill-sets'), {
      method: 'POST',
      params: userScopedParams(),
      data: body,
      headers: { 'Idempotency-Key': `${botId}-${Date.now()}-${Math.random().toString(36).slice(2)}` },
    }),
  updateSkillSet: (botId: string, setId: string, body: { name?: string; description?: string }) =>
    request<SkillSetDto>(path(botId, `skill-sets/${setId}`), 'PUT', {}, body),
  deleteSkillSet: (botId: string, setId: string) => request(path(botId, `skill-sets/${setId}`), 'DELETE'),
  setSkillSetActive: (botId: string, setId: string, active: boolean) =>
    request<SkillSetDto>(path(botId, `skill-sets/${setId}/${active ? 'activate' : 'deactivate'}`), 'POST'),
  listSkillSetSkills: (botId: string, setId: string, ownerId?: string) =>
    request<Array<{ skill_id: string; name: string; description?: string }>>(
      path(botId, `skill-sets/${setId}/skills`),
      'GET',
      { owner_id: ownerId },
    ),
  setSkillSetSkill: (botId: string, setId: string, skillId: string, active: boolean) =>
    request(path(botId, `skill-sets/${setId}/skills/${skillId}`), active ? 'PUT' : 'DELETE'),
  listSkillSetMcps: (botId: string, setId: string) =>
    request<Array<{ server_code: string; name: string; description?: string }>>(
      path(botId, `skill-sets/${setId}/mcps`),
    ),
  setSkillSetMcp: (botId: string, setId: string, serverCode: string, active: boolean) =>
    request(path(botId, `skill-sets/${setId}/mcps/${encodeURIComponent(serverCode)}`), active ? 'PUT' : 'DELETE'),
  listResources: (botId: string, directory = '', ownerId?: string) =>
    request<BackendApiPage<ResourceDto>>(path(botId, 'resources'), 'GET', {
      path: directory,
      page: 1,
      page_size: 100,
      owner_id: ownerId,
    }),
  createDirectory: (botId: string, resourcePath: string, ownerId?: string) =>
    request<ResourceDto>(path(botId, 'resources/mkdir'), 'POST', { path: resourcePath, owner_id: ownerId }),
  deleteResource: (botId: string, resourcePath: string, ownerId?: string) =>
    request(path(botId, 'resources'), 'DELETE', { path: resourcePath, owner_id: ownerId }),
  uploadResource: (botId: string, resourcePath: string, content: ArrayBuffer, overwrite = false, ownerId?: string) =>
    backendRequest<BackendApiEnvelope<ResourceDto>>(path(botId, 'resources/upload'), {
      method: 'POST',
      params: userScopedParams({ path: resourcePath, overwrite, owner_id: ownerId }),
      rawBody: content,
      headers: { 'Content-Type': 'application/octet-stream' },
    }),
  previewResource: (botId: string, resourcePath: string, ownerId?: string) =>
    request<{ path: string; content_type: string; content: string }>(path(botId, 'resources/preview'), 'GET', {
      path: resourcePath,
      owner_id: ownerId,
    }),
  downloadResource: (botId: string, resourcePath: string, ownerId?: string) =>
    backendRequest<Blob>(path(botId, 'resources/download'), {
      method: 'GET',
      params: userScopedParams({ path: resourcePath, owner_id: ownerId }),
      responseType: 'blob',
    }),
  downloadResourceDirectory: (botId: string, resourcePath: string, ownerId?: string) =>
    backendRequest<Blob>(path(botId, 'resources/download-dir'), {
      method: 'GET',
      params: userScopedParams({ path: resourcePath, owner_id: ownerId }),
      responseType: 'blob',
    }),
  listRoutines: (botId: string, ownerId?: string) =>
    request<BackendApiPage<RoutineDto>>(path(botId, 'routines'), 'GET', {
      page: 1,
      page_size: 100,
      owner_id: ownerId,
    }),
  createRoutine: (botId: string, body: RoutineWrite, ownerId?: string) =>
    request<RoutineDto>(path(botId, 'routines'), 'POST', { owner_id: ownerId }, body),
  updateRoutine: (botId: string, routineId: string, body: Partial<RoutineWrite>, ownerId?: string) =>
    request<RoutineDto>(path(botId, `routines/${routineId}`), 'PATCH', { owner_id: ownerId }, body),
  deleteRoutine: (botId: string, routineId: string, ownerId?: string) =>
    request(path(botId, `routines/${routineId}`), 'DELETE', { owner_id: ownerId }),
  runRoutine: (botId: string, routineId: string, ownerId?: string) =>
    request(path(botId, `routines/${routineId}/run`), 'POST', { owner_id: ownerId }),
  listRoutineRuns: (botId: string, routineId: string, ownerId?: string) =>
    request<BackendApiPage<RoutineRunDto>>(path(botId, `routines/${routineId}/runs`), 'GET', {
      page: 1,
      page_size: 20,
      owner_id: ownerId,
    }),
  listIdentityFiles: (botId: string, ownerId?: string) =>
    request<{ bot_id: string; files: IdentityFileInfoDto[] }>(path(botId, 'identity'), 'GET', {
      owner_id: ownerId,
    }),
  getIdentityFile: (botId: string, type: string) => request<IdentityFileDto>(path(botId, `identity/${type}`)),
  updateIdentityFile: (botId: string, type: string, content: string) =>
    request(path(botId, `identity/${type}`), 'PUT', {}, { content }),
  listChannels: (botId: string, ownerId?: string) =>
    request<ChannelDto[] | BackendApiPage<ChannelDto>>(path(botId, 'channels'), 'GET', {
      page: 1,
      page_size: 100,
      stage: 'draft',
      owner_id: ownerId,
    }),
  createChannel: (botId: string, body: BackendUnknownRecord) =>
    request<ChannelDto>(path(botId, 'channels'), 'POST', {}, body),
  updateChannel: (botId: string, channelId: number, body: BackendUnknownRecord) =>
    request<ChannelDto>(path(botId, `channels/${channelId}`), 'PATCH', {}, body),
  setChannelStatus: (botId: string, channelId: number, status: 'active' | 'inactive') =>
    request<ChannelDto>(path(botId, `channels/${channelId}/status`), 'PUT', {}, { status }),
  deleteChannel: (botId: string, channelId: number) => request(path(botId, `channels/${channelId}`), 'DELETE'),
  getCallerContext: (botId: string, ownerId?: string) =>
    request<{
      editable: boolean;
      mcp_call_types: Record<string, 'caller' | 'owner'>;
      cli_call_types: Record<string, 'caller' | 'owner'>;
    }>(path(botId, 'caller-context'), 'GET', { stage: 'draft', owner_id: ownerId }),
  updateMcpCallType: (botId: string, serverCode: string, callType: 'caller' | 'owner', ownerId?: string) =>
    request<{ server_code: string; call_type: 'caller' | 'owner'; bot_call_type: 'caller' | 'owner' }>(
      path(botId, `mcps/${encodeURIComponent(serverCode)}/call-type`),
      'PATCH',
      { owner_id: ownerId },
      { call_type: callType },
    ),
  getEngineConfig: (botId: string, ownerId?: string) =>
    request<BackendUnknownRecord>(path(botId, 'engine/config'), 'GET', { owner_id: ownerId }),
  getEngineStatus: (botId: string, ownerId?: string) =>
    request<EngineStatusDto>(path(botId, 'engine/status'), 'GET', { owner_id: ownerId }),
  updateEngineConfig: (botId: string, body: BackendUnknownRecord, ownerId?: string) =>
    request<BackendUnknownRecord>(path(botId, 'engine/config'), 'PUT', { owner_id: ownerId }, body),
  getApprovalConfig: (botId: string, ownerId?: string) =>
    request<ApprovalConfigDto>(path(botId, 'lifecycle/approval'), 'GET', { owner_id: ownerId }),
  updateApprovalConfig: (botId: string, enabled: boolean, ownerId?: string) =>
    request<ApprovalConfigDto>(
      path(botId, 'lifecycle/approval'),
      'PUT',
      { owner_id: ownerId },
      {
        should_approval: enabled,
      },
    ),
  listRenderScreens: (botId: string, ownerId?: string) =>
    request<{ total: number; items: RenderScreenDto[] }>(path(botId, 'render-screens'), 'GET', { owner_id: ownerId }),
  createRenderScreen: (botId: string, body: { name: string; cdn_url: string }, ownerId?: string) =>
    request<RenderScreenDto>(path(botId, 'render-screens'), 'POST', { owner_id: ownerId }, body),
  updateRenderScreen: (botId: string, id: number, body: { name: string; cdn_url: string }, ownerId?: string) =>
    request<RenderScreenDto>(path(botId, `render-screens/${id}`), 'PATCH', { owner_id: ownerId }, body),
  deleteRenderScreen: (botId: string, id: number, ownerId?: string) =>
    request(path(botId, `render-screens/${id}`), 'DELETE', { owner_id: ownerId }),
  getLifecycle: (botId: string) =>
    request<{ bot_id: string; items: ServicePublicationDto[] }>(path(botId, 'lifecycle')),
  upgradeLifecycle: (botId: string, publicationId: number) =>
    request<ServicePublicationDto>(path(botId, `lifecycle/${publicationId}/upgrade`), 'POST'),
  advanceLifecycle: (botId: string, stage: 'prestable' | 'online') =>
    request(path(botId, 'lifecycle/advance'), 'POST', {}, { stage }),
  restartLifecycle: (botId: string, stage: 'prestable' | 'online') =>
    request(path(botId, 'lifecycle/restart'), 'POST', {}, { stage }),
  cancelStaging: (botId: string) => request(path(botId, 'lifecycle/cancel-staging'), 'POST'),
  offlineLifecycle: (botId: string) => request(path(botId, 'lifecycle/offline'), 'POST'),
  retryLifecycle: (botId: string) => request(path(botId, 'lifecycle/retry'), 'POST'),
  deleteLifecycleDraft: (botId: string) => request(path(botId, 'lifecycle'), 'DELETE'),
  getEditLock: (botId: string, ownerId?: string) =>
    request<EditLockDto>(path(botId, 'edit-lock'), 'GET', { owner_id: ownerId }),
  /** 成功响应也可能 acquired=false；抢占必须另经用户确认。 */
  acquireEditLock: (botId: string, ownerId?: string) =>
    request<EditLockDto>(path(botId, 'edit-lock'), 'POST', { owner_id: ownerId }),
  stealEditLock: (botId: string, ownerId?: string) =>
    request<EditLockDto>(path(botId, 'edit-lock/steal'), 'POST', { owner_id: ownerId }),
  releaseEditLock: (botId: string, ownerId?: string) =>
    request<{ released: boolean }>(path(botId, 'edit-lock'), 'DELETE', { owner_id: ownerId }),
};
