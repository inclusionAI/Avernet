import type { KeyboardEvent, MouseEvent, MutableRefObject } from 'react';

type CollaborationOpener = 'card' | 'switch';
type CollaborationTriggerEvent = KeyboardEvent<HTMLButtonElement> | MouseEvent<HTMLButtonElement>;

export function createCollaborationOpenerHandler(
  openerRef: MutableRefObject<CollaborationOpener | null>,
  source: CollaborationOpener,
  toggle?: () => void,
) {
  return (event: CollaborationTriggerEvent) => {
    if ('key' in event) {
      if (event.key === 'Enter' || event.key === ' ') openerRef.current = source;
      return;
    }
    if (event.button !== 0) return;
    openerRef.current = source;
    if (source !== 'card') return;
    event.stopPropagation();
    if (event.type === 'click') toggle?.();
  };
}

export function createCollaborationOpenerHandlers(
  openerRef: MutableRefObject<CollaborationOpener | null>,
  source: CollaborationOpener,
  toggle?: () => void,
) {
  const handler = createCollaborationOpenerHandler(openerRef, source, toggle);
  return { onPointerDown: handler, onKeyDown: handler, onClick: handler };
}
