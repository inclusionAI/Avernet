/** Only safe notification identifiers may cross the management error boundary. */
export interface ManagementNotificationKeys {
  toastKey?: string;
  notificationKey?: string;
}
export function managementNotificationKeys(error: unknown): ManagementNotificationKeys {
  if (!error || typeof error !== 'object') return {};
  const metadata = error as ManagementNotificationKeys;
  return {
    ...(typeof metadata.toastKey === 'string' ? { toastKey: metadata.toastKey } : {}),
    ...(typeof metadata.notificationKey === 'string' ? { notificationKey: metadata.notificationKey } : {}),
  };
}
/** Fresh safe message/stack: never retain upstream message, body, response, cause or error instance. */
export class ManagementReadError extends Error implements ManagementNotificationKeys {
  readonly toastKey?: string;
  readonly notificationKey?: string;

  constructor(message: string, keys: ManagementNotificationKeys, aborted = false) {
    super(message);
    this.name = aborted ? 'AbortError' : 'ManagementReadError';
    if (keys.toastKey !== undefined) this.toastKey = keys.toastKey;
    if (keys.notificationKey !== undefined) this.notificationKey = keys.notificationKey;
  }
}
