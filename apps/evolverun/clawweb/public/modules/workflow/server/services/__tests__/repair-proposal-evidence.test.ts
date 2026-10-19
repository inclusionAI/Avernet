import { expect, it } from 'vitest';
import { prepareProposalEvidence } from '../repair-proposal-evidence.js';
import type { RepairItem } from '../../contracts/repair-batch.js';
function item(context: Record<string,unknown>): RepairItem {
 return {itemId:'item',groupKey:'group',proposalKey:'proposal',contentRevision:1,previousItemId:null,proposal:null,
   instruction:'',sources:[],context};
}
it('marks collection clipping and refuses to freeze more than 500 evidence events',()=>{
 const evidence=Array.from({length:501},(_,i)=>({eventId:`e-${i}`,payload:{observation:`value-${i}`}}));
 const original=item({diagnoses:[{flowId:'run',evidence}]});
 const projected=prepareProposalEvidence([original])[0];
 expect(projected.context?.evidencePacket).toMatchObject({clipped:true});
 expect(()=>prepareProposalEvidence([original],true)).toThrow(/证据/);
});
it('detects truncation inside payloads even before the outer context redaction',()=>{
 const original=item({diagnoses:[{flowId:'run',evidence:[{eventId:'nested',payload:{observations:Array.from({length:501},(_,i)=>i)}}]}]});
 expect(prepareProposalEvidence([original])[0].context?.evidencePacket).toMatchObject({clipped:true});
 expect(()=>prepareProposalEvidence([original],true)).toThrow(/证据/);
});
it('retains all 500 allowed events and does not treat removed credential fields as clipping',()=>{
 const evidence=Array.from({length:500},(_,i)=>({eventId:`e-${i}`,payload:{observation:`value-${i}`,apiKey:'dummy'}}));
 const original=item({diagnoses:[{flowId:'run',evidence}]});
 const prepared=prepareProposalEvidence([original],true)[0];
 expect(prepared.context?.evidencePacket).toMatchObject({clipped:false,missingEvidence:false});
 const diagnoses=prepared.context!.diagnoses as Array<{evidence:Array<{payload:Record<string,unknown>}>}>;
 expect(diagnoses[0].evidence).toHaveLength(500);
 expect(diagnoses[0].evidence[499].payload).toEqual({observation:'value-499'});
 expect(JSON.stringify(prepared)).not.toContain('dummy');
});

it('retains complete redacted material independently from the bounded aggregation view',()=>{
 const report='evidence line\n'.repeat(25000);
 const original=item({diagnoses:[{flowId:'run',evidence:[{eventId:'report',payload:{report,apiKey:'dummy'}}]}]});
 const full=prepareProposalEvidence([original],true,{complete:true})[0];
 expect((full.context!.diagnoses as any[])[0].evidence[0].payload.report).toBe(report);
 expect(JSON.stringify(full)).not.toContain('dummy');
 expect(full.context?.evidencePacket).toMatchObject({clipped:false});
});
