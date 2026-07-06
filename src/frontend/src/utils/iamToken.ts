import { getExt } from '@/capabilities';
import { AppExt } from '@/shell/extension';

const IAM_TOKEN_KEY = 'IAM_TOKEN';

export function getIamTokenFromUrl(): string {
  if (typeof window === 'undefined') {
    return '';
  }

  return new URLSearchParams(window.location.search).get(IAM_TOKEN_KEY) || '';
}

/**
 * 调用 getAuth 接口获取 IAM_TOKEN，存入 userStore。
 * facade：委托给 AppExt.iam——开源默认恒返回空串（无内部 IAM 接口），
 * 内部 src/internal/bootstrap/iam.ts extend 走 /api/v1/token/iam。消费方无需改动。
 */
export async function fetchIamToken(): Promise<string> {
  return getExt(AppExt).iam.fetchIamToken();
}

export function applyIamTokenHeader(
  headers?: Record<string, any>,
): Record<string, any> {
  const iamToken = getIamTokenFromUrl();

  if (!iamToken) {
    return headers || {};
  }

  return {
    ...(headers || {}),
    IAM_TOKEN: headers?.IAM_TOKEN || iamToken,
  };
}

export function appendIamTokenToUrl(rawUrl: string): string {
  const iamToken = getIamTokenFromUrl();

  if (!iamToken || !rawUrl || typeof window === 'undefined') {
    return rawUrl;
  }

  try {
    const nextUrl = new URL(rawUrl, window.location.origin);
    nextUrl.searchParams.set(IAM_TOKEN_KEY, iamToken);
    return nextUrl.toString();
  } catch {
    return rawUrl;
  }
}
