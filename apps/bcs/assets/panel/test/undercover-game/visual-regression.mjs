import { sixPlayerFixtures } from './fixtures/six-player.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { JSDOM } from 'jsdom';
import ts from 'typescript';
const require=createRequire(import.meta.url);const api=require('../../dist/index.umd.js');
const baseline=JSON.parse(readFileSync(new URL('./fixtures/visual-baseline.json',import.meta.url),'utf8'));
assert.deepEqual(Object.keys(sixPlayerFixtures),['wideSpeaking','mediumHumanSpeech','mediumVoting','narrowRoster','laterRoundElimination','shortHeightDock','reducedMotion']);
for(const fixture of Object.values(sixPlayerFixtures))assert.equal(fixture.players.length,6);
const ids=['bot-a','bot-b','bot-c','bot-d','bot-e','human'];
for(const key of ['wide','medium']){const fixture=baseline[key];assert.equal(api.roomComposition(fixture.width),fixture.composition);const seats=api.sceneSeatGeometry(ids,fixture.composition);assert.deepEqual(seats.map(s=>[s.x,s.y,s.facing]),fixture.seats);for(let i=0;i<seats.length;i++)for(let j=i+1;j<seats.length;j++){const dx=seats[i].x-seats[j].x,dy=seats[i].y-seats[j].y;assert.ok(Math.hypot(dx,dy)>=20,`${key} seats ${i+1}/${j+1} overlap`)}}
assert.equal(api.roomComposition(baseline.narrow.width),'narrow');assert.equal(api.roomComposition(459),'narrow');assert.equal(api.roomComposition(460),'medium');assert.equal(api.roomComposition(679),'medium');assert.equal(api.roomComposition(680),'wide');
const stable=api.assignBotAppearances(ids);assert.deepEqual(stable,api.assignBotAppearances(ids));assert.equal(api.botAppearanceIndex('bot-a'),api.botAppearanceIndex('bot-a'));for(let i=1;i<ids.length;i++)assert.notEqual(stable[ids[i-1]],stable[ids[i]],'adjacent variants should differ');
const actors=[{actor:{actorId:'a'},kind:'player',state:'completed_speech',latestOutput:{sequence:1},outputHistory:[],publicHistory:[]},{actor:{actorId:'b'},kind:'player',state:'completed_speech',latestOutput:{sequence:2},outputHistory:[],publicHistory:[]},{actor:{actorId:'c'},kind:'player',state:'active_speech',latestOutput:{sequence:0},outputHistory:[],publicHistory:[]}];assert.equal(api.primaryOutputActor(actors),'c');actors[2].latestOutput=undefined;assert.equal(api.primaryOutputActor(actors),'b');
assert.equal(baseline.shortHeight.dockFooter,'visible');assert.equal(baseline.reducedMotion.staticMarkers,true);

// The documented club-theme finale URL must render the completed preview, not a running graph.
const previewSource=readFileSync(new URL('./visual-preview.tsx',import.meta.url),'utf8');
const previewCompiled=ts.transpileModule(previewSource,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText;
const previewDom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'http://local.test/?mode=finished&theme=club'});
Object.assign(globalThis,{window:previewDom.window,document:previewDom.window.document,location:previewDom.window.location,HTMLElement:previewDom.window.HTMLElement,Node:previewDom.window.Node,HTMLTextAreaElement:previewDom.window.HTMLTextAreaElement,HTMLInputElement:previewDom.window.HTMLInputElement,getComputedStyle:previewDom.window.getComputedStyle,IS_REACT_ACT_ENVIRONMENT:true});
HTMLElement.prototype.attachEvent=function(){};HTMLElement.prototype.detachEvent=function(){};
globalThis.ResizeObserver=class{observe(){}disconnect(){}};window.ResizeObserver=globalThis.ResizeObserver;
const previewRequire=(id)=>{
 if(id==='../../src/undercover-game/UndercoverGamePanel')return{__esModule:true,default:api.UndercoverGamePanel};
 if(id==='../../src/undercover-game/themes/ops')return{UndercoverGameOpsPanel:api.UndercoverGameOpsPanel};
 return require(id);
};
const {act}=await import('react');
const previewModule={exports:{}};
await act(async()=>{
 new Function('require','module','exports',previewCompiled)(previewRequire,previewModule,previewModule.exports);
 await new Promise((resolve)=>setTimeout(resolve,25));
});
assert.ok(document.querySelector('[aria-label="游戏结束"]'),'?mode=finished must render the club finale dialog');
assert.match(document.getElementById('root').textContent,/平民阵营获胜|平民胜利/,'finished alias uses the completed finale fixture');
for(const actorId of ['a','human','c','d','e','f'])assert.equal(document.querySelector(`[data-actor-id="${actorId}"]`)?.getAttribute('data-player-state'),'voted',`finished preview maps vote-${actorId} to its player`);
assert.match(document.getElementById('root').textContent,/6\/6 已完成/,'finished preview counts every mapped vote node');
previewDom.window.close();

// Observer-only speech previews must not turn the completed human speech into an action.
const bubblesDom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'http://local.test/?width=560&height=950&mode=bubbles&bubbleSeat=2&theme=club'});
Object.assign(globalThis,{window:bubblesDom.window,document:bubblesDom.window.document,location:bubblesDom.window.location,HTMLElement:bubblesDom.window.HTMLElement,Node:bubblesDom.window.Node,HTMLTextAreaElement:bubblesDom.window.HTMLTextAreaElement,HTMLInputElement:bubblesDom.window.HTMLInputElement,getComputedStyle:bubblesDom.window.getComputedStyle,IS_REACT_ACT_ENVIRONMENT:true});
HTMLElement.prototype.attachEvent=function(){};HTMLElement.prototype.detachEvent=function(){};
globalThis.ResizeObserver=class{observe(){}disconnect(){}};window.ResizeObserver=globalThis.ResizeObserver;
const bubblesModule={exports:{}};
await act(async()=>{
 new Function('require','module','exports',previewCompiled)(previewRequire,bubblesModule,bubblesModule.exports);
 await new Promise((resolve)=>setTimeout(resolve,25));
});
assert.equal(document.querySelector('[data-actor-id="human"]')?.getAttribute('data-player-state'),'completed_speech','bubble preview keeps seat 2 speech displayable');
assert.match(document.querySelector('[data-region="speech-bubble"]')?.textContent??'',/最新 · 2号 你/,'bubbleSeat=2 selects the completed seat 2 speech');
assert.notEqual(document.querySelector('[data-panel-viewport]')?.getAttribute('data-action-active'),'true','observer-only bubble preview must not activate the action dock');
assert.equal(document.querySelector('[data-region="action-dock"] form'),null,'observer-only bubble preview must not render an action form');
bubblesDom.window.close();
console.log('Visual regression baseline passed: deterministic wide/medium/narrow geometry, non-overlap, appearance, output priority, short Dock, reduced motion, finished-preview alias, and observer-only speech preview.');

await import('./speech-layout.mjs');
