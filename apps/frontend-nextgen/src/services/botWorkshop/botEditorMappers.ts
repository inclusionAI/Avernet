import type {
  BotEditorResource,
  BotEditorRoutine,
  BotEditorRoutineInput,
  BotEditorSkill,
  BotRenderScreen,
  ServicePublication,
} from '@/domain/botEditor';
import type {
  RenderScreenDto,
  ResourceDto,
  RoutineDto,
  ServicePublicationDto,
  SkillDto,
} from '@/services/backendApi/bots/botEditorController';

export const mapSkill = (item: SkillDto): BotEditorSkill => ({
  id: item.skill_id,
  name: item.name,
  description: item.description,
  source: 'local',
  active: item.active,
});

export const mapResource = (item: ResourceDto, parentPath = ''): BotEditorResource => ({
  path: item.path,
  parentPath,
  name: item.name,
  type: item.type,
  size: typeof item.size === 'number' && Number.isFinite(item.size) ? item.size : undefined,
});

export const mapScreen = (item: RenderScreenDto): BotRenderScreen => ({
  id: item.id,
  name: item.name,
  cdnUrl: item.cdn_url,
  creatorId: item.creator_id,
  createdAt: item.created_at,
  updatedAt: item.updated_at,
});

export const mapPublication = (item: ServicePublicationDto): ServicePublication => ({
  publicationId: item.publication_id,
  cardId: item.card_id,
  version: item.version,
  status: item.status as ServicePublication['status'],
  internalStatus: item.internal_status,
  liveVersion: item.live_version,
  availableActions: item.available_actions as ServicePublication['availableActions'],
  deployment: item.deployment
    ? {
        action: String(item.deployment.action ?? ''),
        target: String(item.deployment.target ?? ''),
        status: item.deployment.status === 'failed' ? 'failed' : 'running',
        errorMessage: typeof item.deployment.error_message === 'string' ? item.deployment.error_message : undefined,
      }
    : undefined,
  approval: item.approval
    ? {
        required: Boolean(item.approval.required),
        status: typeof item.approval.status === 'string' ? item.approval.status : undefined,
        approvalId: typeof item.approval.approval_id === 'string' ? item.approval.approval_id : undefined,
        approvalUrl: typeof item.approval.approval_url === 'string' ? item.approval.approval_url : undefined,
      }
    : undefined,
  createdAt: item.created_at,
  updatedAt: item.updated_at,
});

export const mapRoutine = (item: RoutineDto): BotEditorRoutine => ({
  id: item.routine_id,
  name: item.name,
  cron: item.trigger.cron,
  command: item.command,
  enabled: item.enabled,
  timezone: item.timezone,
  modifiedAt: item.gmt_modified,
});

export const toRoutineWrite = (input: BotEditorRoutineInput) => ({
  name: input.name,
  trigger: { type: 'schedule' as const, cron: input.cron },
  command: input.command,
  enabled: input.enabled,
  timezone: input.timezone,
});

export const dataOr = <T>(data: T | undefined, fallback: T) => data ?? fallback;
