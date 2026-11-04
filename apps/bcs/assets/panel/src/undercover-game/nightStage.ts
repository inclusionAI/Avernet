export interface NightSeatSlot {
  x: number;
  y: number;
  labelAbove: boolean;
}

/**
 * Art-calibrated seat ring around the oval table for the six-player classic
 * layout. Index order is clockwise starting on the left; game data decides who
 * occupies each index through `seatOrder`.
 */
const CLASSIC_SEAT_SLOTS: readonly NightSeatSlot[] = [
  { x: 20, y: 69, labelAbove: true },
  { x: 35, y: 53, labelAbove: true },
  { x: 64, y: 53, labelAbove: true },
  { x: 79, y: 70, labelAbove: true },
  { x: 62, y: 81, labelAbove: true },
  { x: 36, y: 81, labelAbove: true },
];

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

/** Deterministic ring for any player count; never depends on actor names. */
export function nightSeatSlots(count: number): NightSeatSlot[] {
  if (!Number.isInteger(count) || count <= 0) return [];
  if (count === CLASSIC_SEAT_SLOTS.length) return [...CLASSIC_SEAT_SLOTS];
  const slots: NightSeatSlot[] = [];
  for (let index = 0; index < count; index += 1) {
    const angle = Math.PI * 0.85 + (index / count) * Math.PI * 2;
    const x = 50 + Math.cos(angle) * 31;
    const y = 66 + Math.sin(angle) * 16;
    slots.push({ x: round1(x), y: round1(y), labelAbove: y <= 70 });
  }
  return slots;
}

/** Portrait center of the host/judge, matching the reference composition. */
export const NIGHT_HOST_SLOT = { x: 50, y: 34 } as const;
