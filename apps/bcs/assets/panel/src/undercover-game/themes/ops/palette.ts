import type { FlavorName, FlavorPalette } from './themeTypes';

/**
 * Terminal ops-theme palette. High-contrast amber/teal on near-black,
 * chosen to stay distinguishable under color removal and narrow viewports.
 * These are design tokens only — never secret material.
 */
export const OPS_PALETTE: Record<FlavorName, FlavorPalette> = {
  obsidian: {
    bg: '#0b0e13',
    panel: '#12161d',
    panelAlt: '#0f131a',
    border: '#26303e',
    borderStrong: '#3a4a5e',
    text: '#dfe6ee',
    textInverse: '#0b0e13',
    muted: '#8fa0b5',
    accent: '#ffcf7a',
    accentSoft: '#ffcf7a18',
    accentText: '#7a5b22',
    good: '#4fe0a8',
    goodSoft: '#162e25',
    bad: '#ff6b6b',
    warn: '#ffcf7a',
    running: '#4fb3ff',
    pending: '#66778f',
    done: '#4fe0a8',
    human: '#ffcf7a',
  },
};