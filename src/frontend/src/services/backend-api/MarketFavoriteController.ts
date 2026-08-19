import { request } from '@umijs/max';

export type FavoriteTargetType = 'SKILL';

export interface MarketFavoriteItem {
  favorite_id: number;
  target_type: FavoriteTargetType;
  target_code: string;
  favorite_at: string;
  is_favorited: boolean;
}

interface Page<T> {
  total: number;
  items: T[];
}

interface Envelope<T> {
  code: number;
  message: string;
  data: T;
}

interface FavoriteTarget {
  target_type: FavoriteTargetType;
  target_code: string;
}

interface FavoriteAddedResult extends FavoriteTarget {
  favorite_id: number;
  is_favorited: boolean;
}

function endpoint(spaceId: number, suffix = ''): string {
  return `/openapi/v1/spaces/${spaceId}/market-favorites${suffix}`;
}

export function listMarketFavorites(
  spaceId: number,
  userId: string,
): Promise<Envelope<Page<MarketFavoriteItem>>> {
  return request(endpoint(spaceId, '/search'), {
    method: 'POST',
    params: { user_id: userId },
    data: { page_no: 1, page_size: 100 },
  });
}

export function addMarketFavorite(
  spaceId: number,
  userId: string,
  target: FavoriteTarget,
): Promise<Envelope<FavoriteAddedResult>> {
  return request(endpoint(spaceId), {
    method: 'POST',
    params: { user_id: userId },
    data: target,
  });
}

export function cancelMarketFavorite(
  spaceId: number,
  userId: string,
  target: FavoriteTarget,
): Promise<Envelope<FavoriteTarget>> {
  return request(endpoint(spaceId, '/cancel'), {
    method: 'POST',
    params: { user_id: userId },
    data: target,
  });
}
