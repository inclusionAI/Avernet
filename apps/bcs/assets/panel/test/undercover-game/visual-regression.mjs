import { sixPlayerFixtures } from './fixtures/six-player.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
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
console.log('Visual regression baseline passed: deterministic wide/medium/narrow geometry, non-overlap, appearance, output priority, short Dock, and reduced motion.');

await import('./speech-layout.mjs');
