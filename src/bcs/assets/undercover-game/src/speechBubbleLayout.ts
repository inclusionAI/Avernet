export interface SpeechRect { x:number; y:number; width:number; height:number }
export interface BubblePlacement extends SpeechRect { side:'left'|'right'|'above'|'below'; tail:number }
const clamp=(value:number,min:number,max:number)=>Math.max(min,Math.min(max,value));
const overlap=(a:SpeechRect,b:SpeechRect)=>Math.max(0,Math.min(a.x+a.width,b.x+b.width+4)-Math.max(a.x,b.x-4))*Math.max(0,Math.min(a.y+a.height,b.y+b.height+4)-Math.max(a.y,b.y-4));

/** Keep one expanded bubble near its speaker, clear of the measured player labels. */
export function placeSpeechBubble(room:SpeechRect,speaker:SpeechRect,obstacles:SpeechRect[]):BubblePlacement {
  const preferredWidth=room.width>=640?216:room.width>=500?164:132;
  const head={x:speaker.x+speaker.width/2,y:speaker.y+24};
  const candidates:BubblePlacement[]=[];
  for(const width of [...new Set([preferredWidth,132])])for(const height of [96,80,74]){
  const add=(x:number,y:number,side:BubblePlacement['side'])=>{
    const box={x:clamp(x,10,room.width-width-10),y:clamp(y,10,room.height-height-10),width,height,side,tail:0};
    box.tail=side==='left'||side==='right'?clamp(head.y-box.y-12,8,height-32):clamp(head.x-box.x-12,8,width-32);
    candidates.push(box);
  };
  const inward=head.x<room.width/2?'right':'left';
  for(const side of [inward,inward==='right'?'left':'right'] as const){
    for(const y of [head.y-height/2,speaker.y+16,speaker.y+24,speaker.y-height+20]){
      add(side==='right'?speaker.x+speaker.width+8:speaker.x-width-8,y,side);
    }
  }
  for(const side of ['above','below'] as const){
    for(const x of [head.x-width/2,head.x-width+28,head.x-28,room.width/2-width/2,head.x<room.width/2?10:room.width-width-10]){
      add(x,side==='above'?speaker.y-height-4:speaker.y+speaker.height+14,side);
    }
  }
  }
  const inward=head.x<room.width/2?'right':'left';
  const score=(box:BubblePlacement)=>{
    const collision=obstacles.reduce((sum,rect)=>sum+overlap(box,rect),0);
    const dx=Math.max(box.x-head.x,0,head.x-box.x-box.width);
    const dy=Math.max(box.y-head.y,0,head.y-box.y-box.height);
    return collision*1000+Math.hypot(dx,dy)+(box.side===inward?0:8)+(preferredWidth-box.width)*.3+(96-box.height)*.3;
  };
  return candidates.sort((a,b)=>score(a)-score(b))[0];
}
