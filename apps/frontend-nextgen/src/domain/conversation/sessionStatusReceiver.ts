import type { SessionStatusEvent } from './sessionStatusProtocol';

/** Validate the optional cross-application callback before exposing presentation data. */
export function isSessionStatusEvent(value: unknown): value is SessionStatusEvent {
  if (!value || typeof value !== 'object') return false;
  const event = value as SessionStatusEvent;
  if (
    event.version !== 1 ||
    typeof event.contextId !== 'string' ||
    typeof event.botId !== 'string' ||
    !Number.isSafeInteger(event.sequence) ||
    event.sequence <= 0
  )
    return false;
  if (event.type === 'reset') return true;
  if (event.type !== 'patch') return false;
  if (event.snapshot !== undefined && typeof event.snapshot !== 'boolean') return false;
  return (
    Array.isArray(event.items) &&
    event.items.length <= 1000 &&
    event.items.every(
      (item) =>
        item &&
        typeof item.sessionId === 'string' &&
        item.sessionId.length > 0 &&
        (item.status === null ||
          (item.status &&
            typeof item.status.code === 'string' &&
            item.status.code.length > 0 &&
            item.status.code.length <= 100 &&
            typeof item.status.label === 'string' &&
            item.status.label.length > 0 &&
            item.status.label.length <= 200 &&
            ['neutral', 'info', 'warning', 'success', 'danger'].includes(item.status.tone))),
    )
  );
}

export function createSessionStatusReceiver(
  contextId: string,
  botId: string,
  apply: (event: SessionStatusEvent) => void,
) {
  let active = false;
  let lastSequence = 0;
  return {
    activate() {
      active = true;
    },
    dispose() {
      active = false;
    },
    receive(value: unknown) {
      if (
        !active ||
        !isSessionStatusEvent(value) ||
        value.contextId !== contextId ||
        value.botId !== botId ||
        value.sequence <= lastSequence
      )
        return;
      lastSequence = value.sequence;
      apply(value);
    },
  };
}
