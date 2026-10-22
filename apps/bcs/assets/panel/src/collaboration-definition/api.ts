import type { CollaborationDefinitionComponentProps } from './types';

// Host configuration stays outside the strict snapshot/reference payload.
// Keep absence distinct from the local default so a side-panel host can inject it.
export function resolveHostApiBaseUrl(props: CollaborationDefinitionComponentProps): string | undefined {
  const data = props.data ?? props.params?.data ?? props.payload?.data;
  return props.apiBaseUrl || props.baseUrl || data?.apiBaseUrl || data?.baseUrl || undefined;
}

export function collaborationApiUrl(baseUrl: string, path: string): string {
  const base = baseUrl.replace(/\/+$/, '');
  // Hosts may expose the BCS root or the collaboration API namespace itself.
  return `${base}${base.endsWith('/collaboration') ? '' : '/collaboration'}/${path}`;
}
