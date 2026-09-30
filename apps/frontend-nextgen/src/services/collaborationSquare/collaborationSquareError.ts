import type { CollaborationSquareErrorCode } from '@/domain/collaborationSquare/types';

export class CollaborationSquareError extends Error {
  constructor(public readonly code: CollaborationSquareErrorCode, message: string) {
    super(message);
    this.name = 'CollaborationSquareError';
  }
}
