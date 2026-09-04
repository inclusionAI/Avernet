import type { ActorViewModel, PlayerState } from './types';
export const stateLabels:Record<PlayerState,string>={waiting:'◌ 等待',action_required:'! 需要你操作',active_speech:'▶ 发言中',completed_speech:'✓ 已发言',waiting_for_vote:'◇ 等待投票',voted:'▣ 已投票',eliminated:'✕ 已淘汰',retrying:'↻ 需要重试',error:'! 出错'};
export function primaryOutputActor(actors:ActorViewModel[]):string|undefined{
 const active=actors.find(a=>a.kind==='player'&&(a.state==='active_speech'||a.state==='action_required')&&a.latestOutput);if(active)return active.actor.actorId;
 return actors.filter(a=>a.kind==='player'&&a.latestOutput&&a.state!=='voted').sort((a,b)=>(b.latestOutput?.sequence??b.latestOutput?.timestamp??0)-(a.latestOutput?.sequence??a.latestOutput?.timestamp??0))[0]?.actor.actorId;
}
