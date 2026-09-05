import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source=readFileSync(new URL('../src/speechBubbleLayout.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2020}}).outputText;
const {placeSpeechBubble}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const intersects=(a,b)=>Math.max(0,Math.min(a.x+a.width,b.x+b.width)-Math.max(a.x,b.x))*Math.max(0,Math.min(a.y+a.height,b.y+b.height)-Math.max(a.y,b.y));
for(const width of [426,460,524,648,864]){
 const seats=width>=640?[[72,47],[86,64],[70,83],[30,83],[14,64],[28,47]]:[[70,45],[80,64],[65,83],[35,83],[20,64],[30,45]];
 const players=seats.map(([x,y])=>({x:width*x/100-52,y:520*y/100-56,width:104,height:112}));
 const obstacles=[...players,{x:width/2-65,y:10,width:130,height:154}];
 for(const [index,speaker] of players.entries()){
   const bubble=placeSpeechBubble({x:0,y:0,width,height:520},speaker,obstacles);
   assert.ok(bubble.x>=0&&bubble.y>=0&&bubble.x+bubble.width<=width&&bubble.y+bubble.height<=520);
   for(const obstacle of obstacles)assert.equal(intersects(bubble,obstacle),0,`${width}px seat ${index+1} must not cover a player or host`);
   assert.deepEqual(bubble,placeSpeechBubble({x:0,y:0,width,height:520},speaker,obstacles));
 }
}
console.log('Speech bubbles: all six seats remain unobscured across five panel widths.');
