import type { ReactNode } from 'react';

/** Display projection only; never replace the stored or outgoing message with this content. */
export interface UserMessagePresentation {
  content: string;
  header: ReactNode;
}

/** Synchronous, pure and zero-I/O. Return null for unrecognized content. */
export type UserMessagePresenter = (content: string) => UserMessagePresentation | null;
