export type RoomComposition='wide'|'medium'|'narrow';
export type Facing='front'|'rear'|'left'|'right';
export interface SceneSeatGeometry{actorId:string;x:number;y:number;facing:Facing;depth:number;chairVariant:Facing;labelAnchor:'above'|'below'|'left'|'right';bubbleAnchor:{x:number;y:number}}
export function roomComposition(width:number):RoomComposition{return width>=680?'wide':width>=460?'medium':'narrow'}
export function sceneSeatGeometry(actorIds:string[],composition:RoomComposition):SceneSeatGeometry[]{
 const wide=[[72,47,'rear'],[86,64,'left'],[70,83,'front'],[30,83,'front'],[14,64,'right'],[28,47,'rear']] as const;
 const medium=[[70,45,'rear'],[80,64,'left'],[65,83,'front'],[35,83,'front'],[20,64,'right'],[30,45,'rear']] as const;
 const source=composition==='wide'?wide:medium;
 return actorIds.map((actorId,index)=>{const preset=source[index]??[12+(index%3)*38,42+Math.floor(index/3)*34,'front'] as const;const [x,y,facing]=preset;return{actorId,x,y,facing,depth:Math.round(y),chairVariant:facing,labelAnchor:y>68?'below':'above',bubbleAnchor:{x,y:y-13}}});
}
