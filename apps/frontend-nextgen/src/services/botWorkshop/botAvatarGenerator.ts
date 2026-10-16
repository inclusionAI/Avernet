import * as bottts from '@dicebear/bottts';
import { createAvatar } from '@dicebear/core';

export function generateBotAvatar(seed: string, index = 0): string {
  return createAvatar(bottts, {
    seed: `${seed || 'bot'}-${index}`,
    size: 80,
    backgroundColor: ['b6e3f4', 'c0aede', 'd1d4f9', 'ffd5dc', 'ffdfbf'],
  }).toDataUri();
}

export function generateBotAvatarBatch(seed: string, batch: number): string[] {
  return Array.from({ length: 8 }, (_, index) => generateBotAvatar(seed, batch * 8 + index));
}
