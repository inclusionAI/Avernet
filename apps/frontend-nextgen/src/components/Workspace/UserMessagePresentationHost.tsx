import { getCapabilities } from '@/capabilities';
import type { ReactNode } from 'react';

/** Deployment-owned display only. Message actions keep using their original message. */
export function UserMessagePresentationHost({ content, children }: {
  content: string;
  children: (content: string) => ReactNode;
}) {
  const capability = getCapabilities().getUserMessagePresentation?.();
  const presentation = capability?.status === 'available' ? capability.value?.(content) : null;
  if (!presentation) return children(content);
  return <>{presentation.header}{children(presentation.content)}</>;
}
