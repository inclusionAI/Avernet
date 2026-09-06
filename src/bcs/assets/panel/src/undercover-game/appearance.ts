export const BOT_VARIANT_COUNT=8;
export function stableAppearanceHash(actorId:string):number{let hash=2166136261;for(const char of actorId){hash^=char.codePointAt(0)??0;hash=Math.imul(hash,16777619)}return hash>>>0}
export function botAppearanceIndex(actorId:string,count=BOT_VARIANT_COUNT):number{return count>0?stableAppearanceHash(actorId)%count:0}
export function assignBotAppearances(actorIds:string[],count=BOT_VARIANT_COUNT):Record<string,number>{const result:Record<string,number>={};let previous=-1;actorIds.forEach((id,index)=>{let variant=botAppearanceIndex(id,count);if(count>1&&variant===previous)variant=(variant+1+(stableAppearanceHash(`${id}:${index}`)%(count-1)))%count;result[id]=variant;previous=variant});return result}
