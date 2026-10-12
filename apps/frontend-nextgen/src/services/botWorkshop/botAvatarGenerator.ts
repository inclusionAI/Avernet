const BACKGROUND_COLORS = ['#b6e3f4', '#c0aede', '#d1d4f9', '#ffd5dc', '#ffdfbf'];

function hashSeed(value: string): number {
  return Array.from(value).reduce((hash, char) => (hash * 31 + char.charCodeAt(0)) >>> 0, 0);
}

export function generateBotAvatar(seed: string, index = 0): string {
  const hash = hashSeed(`${seed || 'bot'}-${index}`);
  const background = BACKGROUND_COLORS[hash % BACKGROUND_COLORS.length];
  const eyeOffset = 1 + (hash % 3);
  const antennaOffset = (hash % 9) - 4;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80"><rect width="80" height="80" rx="16" fill="${background}"/><path d="M40 13v9M${
    40 + antennaOffset
  } 13a4 4 0 1 0 0-8 4 4 0 0 0 0 8" stroke="#334155" stroke-width="4" stroke-linecap="round" fill="none"/><rect x="16" y="22" width="48" height="42" rx="13" fill="#fff" stroke="#334155" stroke-width="4"/><circle cx="${
    30 - eyeOffset
  }" cy="40" r="4" fill="#2563eb"/><circle cx="${
    50 + eyeOffset
  }" cy="40" r="4" fill="#2563eb"/><path d="M29 52c6 5 16 5 22 0" stroke="#334155" stroke-width="4" stroke-linecap="round" fill="none"/></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

export function generateBotAvatarBatch(seed: string, batch: number): string[] {
  return Array.from({ length: 8 }, (_, index) => generateBotAvatar(seed, batch * 8 + index));
}
