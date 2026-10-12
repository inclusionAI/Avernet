import type { BrowseSubscription, BrowseSubscriptionDto } from './types';

/** snake_case DTO → camelCase 领域视图。note 缺省归一为 null（UI 按空串展示）。 */
export function mapBrowseSubscriptionDto(dto: BrowseSubscriptionDto): BrowseSubscription {
  return {
    botId: dto.bot_id,
    ownerUserId: dto.owner_user_id,
    mode: dto.mode,
    note: dto.note ?? null,
  };
}
