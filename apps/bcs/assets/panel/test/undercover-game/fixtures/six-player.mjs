const players=[
 {actorId:'bot-a',displayName:'小甲',seatNumber:1},{actorId:'bot-b',displayName:'小乙',seatNumber:2},
 {actorId:'bot-c',displayName:'小丙',seatNumber:3},{actorId:'bot-d',displayName:'小丁',seatNumber:4},
 {actorId:'bot-e',displayName:'小戊',seatNumber:5},{actorId:'human',displayName:'玩家',seatNumber:6,isHuman:true},
];
const base={runId:'visual-run',groupId:'visual-group',sessionId:'visual-session',gameSessionId:'visual-game',round:1,attempt:1,host:{actorId:'host',displayName:'主持人'},players,seatOrder:players.map(p=>p.actorId),turnOrder:players.map(p=>p.actorId),nodeActorMap:Object.fromEntries(players.map(p=>[`speech-${p.actorId}`,p.actorId])),currentViewerActorId:'human',rules:{speechMaxChars:25,forbidOwnWord:true},publicHistory:[],autoRefresh:false};
export const sixPlayerFixtures={
 wideSpeaking:{...base,phase:'speaking',fixtureWidth:760,activeActorId:'bot-c'},
 mediumHumanSpeech:{...base,phase:'speaking',fixtureWidth:560,currentAction:{actorId:'human',type:'speech',nodeId:'speech-human'}},
 mediumVoting:{...base,phase:'voting',fixtureWidth:560,voteCandidates:players.filter(p=>p.actorId!=='human').map(p=>({...p,eligible:true}))},
 narrowRoster:{...base,phase:'speaking',fixtureWidth:420},
 laterRoundElimination:{...base,phase:'speaking',round:3,fixtureWidth:760,players:players.map(p=>p.actorId==='bot-b'?{...p,alive:false,eliminated:true}:p),turnOrder:players.filter(p=>p.actorId!=='bot-b').map(p=>p.actorId)},
 shortHeightDock:{...base,phase:'speaking',fixtureWidth:720,fixtureHeight:280,currentAction:{actorId:'human',type:'speech',nodeId:'speech-human'}},
 reducedMotion:{...base,phase:'speaking',fixtureWidth:760,reducedMotion:true,activeActorId:'bot-a'},
};
