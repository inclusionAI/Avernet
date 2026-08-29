import type { AuthProvidersDto, AuthUserDto } from './authApiController';

export interface AuthUser {
  userId: string;
  displayName: string;
  provider: string;
  avatarUrl?: string;
}

export function toAuthUser(dto: AuthUserDto): AuthUser {
  return {
    userId: dto.user_id,
    displayName: dto.name?.trim() || dto.user_id,
    provider: dto.provider,
    avatarUrl: dto.avatar || undefined,
  };
}

export function selectAlipayLoginUrl(dto: AuthProvidersDto): string | null {
  return dto.providers.find((provider) => provider.name === 'alipay')?.url || null;
}
