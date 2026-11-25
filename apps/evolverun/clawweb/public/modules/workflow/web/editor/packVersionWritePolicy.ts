import type { Snapshot } from './packVersionSession'

/** Only a trusted host's explicit, per-snapshot capability enables writes. */
export function packVersionWritesEnabled(snapshot: Snapshot | null): boolean { return snapshot?.canWrite === true }
