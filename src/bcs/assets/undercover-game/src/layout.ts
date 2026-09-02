export interface SeatCoordinate {
  actorId: string;
  left: number;
  top: number;
  compactLeft: number;
  compactTop: number;
}

/** Stable clockwise positions: seat 0 is below the host's right side. */
export function getSeatCoordinates(actorIds: string[]): SeatCoordinate[] {
  const count = actorIds.length;
  const centerX = 50;
  const centerY = 62;
  const radiusX = count <= 4 ? 35 : 39;
  const radiusY = count <= 4 ? 25 : 28;
  return actorIds.map((actorId, index) => {
    const angle = Math.PI * 0.18 + (index / count) * Math.PI * 2;
    return {
      actorId,
      left: centerX + Math.cos(angle) * radiusX,
      top: centerY + Math.sin(angle) * radiusY,
      compactLeft: 8 + (index % 2) * 50,
      compactTop: 34 + Math.floor(index / 2) * 22,
    };
  });
}

export function truncateBubbleText(text: string, maxChars = 72): { text: string; truncated: boolean } {
  const chars = Array.from(text);
  if (chars.length <= maxChars) return { text, truncated: false };
  return { text: `${chars.slice(0, Math.max(1, maxChars - 1)).join('')}…`, truncated: true };
}
