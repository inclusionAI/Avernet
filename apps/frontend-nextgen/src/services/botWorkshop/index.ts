export * from './botChatService';
export * from './botContainerService';
export * from './botMapper';
export * from './botPolicy';
export * from './botWorkshopService';
export * from './externalBotService';
export * from './types';

// Re-exported so hooks can branch on a 404 from the detail poll without crossing the
// Component → Hook → Service → API Controller layer boundary: this service layer
// owns the only import of the typed error from the low-level HTTP client.
export { BackendRequestError } from '@/services/backendApi/httpClient';
export type { BackendRequestError as BackendRequestErrorType } from '@/services/backendApi/httpClient';
