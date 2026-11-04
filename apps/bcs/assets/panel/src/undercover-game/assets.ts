import uiAtlasSvg from './assets/ui-atlas.svg?raw';
import nightHallSvg from './assets/night-hall.svg?raw';
import nightPortraitsSvg from './assets/night-portraits.svg?raw';

export type AtlasName = 'ui' | 'night-hall' | 'night-portraits';
export interface SpriteCoordinate {
  atlas: AtlasName;
  x: number;
  y: number;
  width: number;
  height: number;
}

const atlasSources: Record<AtlasName, string> = {
  ui: uiAtlasSvg,
  'night-hall': nightHallSvg,
  'night-portraits': nightPortraitsSvg,
};

function dataUrl(svg: string): string {
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

export const atlasUrls: Record<AtlasName, string> = {
  ui: dataUrl(uiAtlasSvg),
  'night-hall': dataUrl(nightHallSvg),
  'night-portraits': dataUrl(nightPortraitsSvg),
};

const spriteUrlCache = new Map<string, string>();

function atlasBody(svg: string): string {
  return svg.slice(svg.indexOf('>') + 1, svg.lastIndexOf('</svg>'));
}

/** Crop one atlas cell to a standalone data URL without a runtime image request. */
export function spriteUrl(sprite: SpriteCoordinate): string {
  const key = `${sprite.atlas}:${sprite.x}:${sprite.y}:${sprite.width}:${sprite.height}`;
  const cached = spriteUrlCache.get(key);
  if (cached) return cached;
  const cropped = `<svg xmlns="http://www.w3.org/2000/svg" width="${sprite.width}" height="${sprite.height}" viewBox="${sprite.x} ${sprite.y} ${sprite.width} ${sprite.height}" preserveAspectRatio="none" shape-rendering="crispEdges">${atlasBody(atlasSources[sprite.atlas])}</svg>`;
  const url = dataUrl(cropped);
  spriteUrlCache.set(key, url);
  return url;
}

export const sprites = {
  viewer: { atlas: 'ui', x: 0, y: 0, width: 32, height: 32 },
  action: { atlas: 'ui', x: 32, y: 0, width: 32, height: 32 },
  speaking: { atlas: 'ui', x: 64, y: 0, width: 32, height: 32 },
  completed: { atlas: 'ui', x: 96, y: 0, width: 32, height: 32 },
  voted: { atlas: 'ui', x: 128, y: 0, width: 32, height: 32 },
  eliminated: { atlas: 'ui', x: 160, y: 0, width: 32, height: 32 },
  retrying: { atlas: 'ui', x: 192, y: 0, width: 32, height: 32 },
  error: { atlas: 'ui', x: 224, y: 0, width: 32, height: 32 },
  bubble: { atlas: 'ui', x: 0, y: 32, width: 32, height: 32 },
  secretCard: { atlas: 'ui', x: 32, y: 32, width: 32, height: 32 },
  voteCard: { atlas: 'ui', x: 64, y: 32, width: 32, height: 32 },
} as const satisfies Record<string, SpriteCoordinate>;

export const NIGHT_PORTRAIT_VARIANTS = 6;
export const NIGHT_PORTRAIT_COLUMNS = 5;
const NIGHT_PORTRAIT_CELL = 80;

export function nightPortraitSprite(variant: number, eliminated = false): SpriteCoordinate {
  const normalized = ((Math.trunc(variant) % NIGHT_PORTRAIT_VARIANTS) + NIGHT_PORTRAIT_VARIANTS) % NIGHT_PORTRAIT_VARIANTS;
  const index = (eliminated ? NIGHT_PORTRAIT_VARIANTS : 0) + normalized;
  return {
    atlas: 'night-portraits',
    x: (index % NIGHT_PORTRAIT_COLUMNS) * NIGHT_PORTRAIT_CELL,
    y: Math.floor(index / NIGHT_PORTRAIT_COLUMNS) * NIGHT_PORTRAIT_CELL,
    width: NIGHT_PORTRAIT_CELL,
    height: NIGHT_PORTRAIT_CELL,
  };
}

/** Judge/host portrait cell (index 12 in the 5-column portrait atlas). */
export const nightJudgePortrait: SpriteCoordinate = {
  atlas: 'night-portraits',
  x: 2 * NIGHT_PORTRAIT_CELL,
  y: 2 * NIGHT_PORTRAIT_CELL,
  width: NIGHT_PORTRAIT_CELL,
  height: NIGHT_PORTRAIT_CELL,
};
