import roomAtlasSvg from './assets/room-atlas.svg?raw';
import characterAtlasSvg from './assets/character-atlas.svg?raw';
import uiAtlasSvg from './assets/ui-atlas.svg?raw';

export type AtlasName = 'room' | 'character' | 'ui';
export interface SpriteCoordinate { atlas: AtlasName; x: number; y: number; width: number; height: number }
export const atlasUrls: Record<AtlasName, string> = {
  room: `data:image/svg+xml,${encodeURIComponent(roomAtlasSvg)}`,
  character: `data:image/svg+xml,${encodeURIComponent(characterAtlasSvg)}`,
  ui: `data:image/svg+xml,${encodeURIComponent(uiAtlasSvg)}`,
};

const atlasSvg: Record<AtlasName, string> = { room: roomAtlasSvg, character: characterAtlasSvg, ui: uiAtlasSvg };
const spriteUrlCache = new Map<string, string>();
function atlasBody(svg: string): string { return svg.slice(svg.indexOf('>') + 1, svg.lastIndexOf('</svg>')); }
export function spriteUrl(sprite: SpriteCoordinate): string {
  const key = `${sprite.atlas}:${sprite.x}:${sprite.y}:${sprite.width}:${sprite.height}`;
  const cached = spriteUrlCache.get(key);
  if (cached) return cached;
  const cropped = `<svg xmlns="http://www.w3.org/2000/svg" width="${sprite.width}" height="${sprite.height}" viewBox="${sprite.x} ${sprite.y} ${sprite.width} ${sprite.height}" preserveAspectRatio="none" shape-rendering="crispEdges">${atlasBody(atlasSvg[sprite.atlas])}</svg>`;
  const url = `data:image/svg+xml,${encodeURIComponent(cropped)}`;
  spriteUrlCache.set(key, url);
  return url;
}

export const sprites = {
  wall:{atlas:'room',x:0,y:0,width:32,height:32},floor:{atlas:'room',x:32,y:0,width:32,height:32},rug:{atlas:'room',x:64,y:0,width:32,height:32},table:{atlas:'room',x:96,y:0,width:32,height:32},
  chairFront:{atlas:'room',x:128,y:0,width:32,height:32},chairRear:{atlas:'room',x:160,y:0,width:32,height:32},chairLeft:{atlas:'room',x:192,y:0,width:32,height:32},chairRight:{atlas:'room',x:224,y:0,width:32,height:32},
  podium:{atlas:'room',x:0,y:32,width:32,height:32},window:{atlas:'room',x:32,y:32,width:32,height:32},lamp:{atlas:'room',x:64,y:32,width:32,height:32},clock:{atlas:'room',x:96,y:32,width:32,height:32},shelf:{atlas:'room',x:128,y:40,width:16,height:16},plant:{atlas:'room',x:160,y:40,width:16,height:16},frame:{atlas:'room',x:192,y:40,width:16,height:16},shadow:{atlas:'room',x:224,y:32,width:32,height:32},
  humanFrame:{atlas:'character',x:128,y:0,width:32,height:32},host:{atlas:'character',x:160,y:0,width:32,height:32},actorShadow:{atlas:'character',x:192,y:0,width:32,height:32},speakingAlt:{atlas:'character',x:224,y:0,width:32,height:32},
  viewer:{atlas:'ui',x:0,y:0,width:32,height:32},action:{atlas:'ui',x:32,y:0,width:32,height:32},speaking:{atlas:'ui',x:64,y:0,width:32,height:32},completed:{atlas:'ui',x:96,y:0,width:32,height:32},voted:{atlas:'ui',x:128,y:0,width:32,height:32},eliminated:{atlas:'ui',x:160,y:0,width:32,height:32},retrying:{atlas:'ui',x:192,y:0,width:32,height:32},error:{atlas:'ui',x:224,y:0,width:32,height:32},bubble:{atlas:'ui',x:0,y:32,width:32,height:32},secretCard:{atlas:'ui',x:32,y:32,width:32,height:32},voteCard:{atlas:'ui',x:64,y:32,width:32,height:32},
} as const satisfies Record<string, SpriteCoordinate>;
export function botSprite(index:number):SpriteCoordinate{return{atlas:'character',x:(index%4)*32,y:Math.floor(index/4)*32,width:32,height:32}}
