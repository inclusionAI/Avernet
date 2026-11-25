import {
  botEditorController,
  type ServicePublicationDto,
} from '@/services/backendApi/bots/botEditorController';

export type ServiceBotPublishStage = 'verify' | 'online';

export interface ServiceBotPublishSelection {
  publishId: string;
  stage: ServiceBotPublishStage;
}

interface ResolveServiceBotPublishSelectionInput {
  botId: string;
  ownerId?: string;
  stage: ServiceBotPublishStage;
}

const PREFERRED_PUBLICATION_STATUS: Record<ServiceBotPublishStage, string> = {
  verify: 'prestable',
  online: 'running',
};

const STAGE_LABEL: Record<ServiceBotPublishStage, string> = {
  verify: '预发',
  online: '线上',
};

function normalizePublicationId(value: number | string): number | undefined {
  const publicationId = typeof value === 'number' ? value : Number(value);
  return Number.isSafeInteger(publicationId) && publicationId > 0 ? publicationId : undefined;
}

export function selectServiceBotPublishSelection(
  publications: ServicePublicationDto[],
  stage: ServiceBotPublishStage,
): ServiceBotPublishSelection | undefined {
  const expectedStatus = PREFERRED_PUBLICATION_STATUS[stage];
  const publication = publications.find(
    item =>
      String(item.status ?? '').toLowerCase() === expectedStatus &&
      normalizePublicationId(item.publication_id) !== undefined,
  );
  const publicationId = publication ? normalizePublicationId(publication.publication_id) : undefined;
  return publicationId === undefined ? undefined : { publishId: String(publicationId), stage };
}

/**
 * Resolve the exact service publication AIXCoding must address.
 *
 * Chat directories only carry `stage=online`; they do not expose a publish id.
 * Lifecycle cards do. They are also addressed with the Bot's owner/entity id, so
 * a collaborator reading a team-owned service Bot sends the current acting user
 * with that owner locator rather than confusing the caller with the owner.
 */
export async function resolveServiceBotPublishSelection({
  botId,
  ownerId,
  stage,
}: ResolveServiceBotPublishSelectionInput): Promise<ServiceBotPublishSelection> {
  if (!botId) throw new Error('服务 Coding Bot 缺少 botId，无法解析运行环境');

  const response = await botEditorController.getLifecycle(botId, ownerId);
  const publications = response.data?.items ?? [];
  const selection = selectServiceBotPublishSelection(publications, stage);
  if (!selection) {
    throw new Error(`未找到可用的${STAGE_LABEL[stage]}服务发布单`);
  }
  return selection;
}
