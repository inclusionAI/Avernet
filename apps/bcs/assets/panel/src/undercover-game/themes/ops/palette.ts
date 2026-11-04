import type { FlavorName, FlavorPalette } from './themeTypes';

/**
 * Ops-theme palette. Every surface, accent and status colour resolves from the
 * workbench's shared shadcn semantic tokens (`hsl(var(--token))`), so the panel
 * follows the host theme instead of shipping an independent literal palette.
 * Token definitions live in `apps/frontend-nextgen/src/global.css`.
 *
 * Fallbacks only apply when the host does not define the token; they mirror the
 * shared dark values so an isolated preview keeps the high-contrast terminal
 * look. These are design tokens only — never secret material.
 */
const token = (name: string, fallback: string): string => `hsl(var(--${name}, ${fallback}))`;
const tokenAlpha = (name: string, fallback: string, alpha: number): string => `hsl(var(--${name}, ${fallback}) / ${alpha})`;

const BACKGROUND = '240 10% 3.9%';
const FOREGROUND = '0 0% 98%';
const CARD = '240 10% 3.9%';
const MUTED = '240 3.7% 15.9%';
const MUTED_FOREGROUND = '240 5% 64.9%';
const BORDER = '240 3.7% 15.9%';
const PRIMARY = '221.7 100% 62%';
const PRIMARY_FOREGROUND = '0 0% 98%';
const SUCCESS = '142 71% 45%';
const WARNING = '38 92% 50%';
const INFO = '199 89% 48%';
const DESTRUCTIVE = '0 72.2% 50.6%';

export const OPS_PALETTE: Record<FlavorName, FlavorPalette> = {
  obsidian: {
    bg: token('background', BACKGROUND),
    panel: token('card', CARD),
    panelAlt: token('muted', MUTED),
    border: token('border', BORDER),
    borderStrong: tokenAlpha('foreground', FOREGROUND, 0.28),
    text: token('foreground', FOREGROUND),
    textInverse: token('primary-foreground', PRIMARY_FOREGROUND),
    muted: token('muted-foreground', MUTED_FOREGROUND),
    accent: token('primary', PRIMARY),
    accentSoft: tokenAlpha('primary', PRIMARY, 0.14),
    accentText: token('primary', PRIMARY),
    good: token('success', SUCCESS),
    goodSoft: tokenAlpha('success', SUCCESS, 0.14),
    bad: token('destructive', DESTRUCTIVE),
    badSoft: tokenAlpha('destructive', DESTRUCTIVE, 0.14),
    warn: token('warning', WARNING),
    warnSoft: tokenAlpha('warning', WARNING, 0.16),
    running: token('info', INFO),
    runningSoft: tokenAlpha('info', INFO, 0.16),
    pending: token('muted-foreground', MUTED_FOREGROUND),
    pendingSoft: tokenAlpha('muted-foreground', MUTED_FOREGROUND, 0.14),
    done: token('success', SUCCESS),
    doneSoft: tokenAlpha('success', SUCCESS, 0.16),
    human: token('primary', PRIMARY),
  },
};