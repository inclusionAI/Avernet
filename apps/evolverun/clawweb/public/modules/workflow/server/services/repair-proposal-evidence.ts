import { redactEvidenceValue } from '@avernet/clawweb-shared/server/services/redaction';
import { canonicalRepairJson, digestRepairJson, RepairBatchError, type RepairItem } from '../contracts/repair-batch.js';
/** Compare retained fields only: intentional credential removal is not clipping. */
function collectionClipped(before:unknown, after:unknown):boolean {
  if(Array.isArray(before)&&Array.isArray(after))return before.length!==after.length||after.some((value,index)=>collectionClipped(before[index],value));
  if(before&&after&&typeof before==='object'&&typeof after==='object')return Object.entries(after).some(([key,value])=>collectionClipped((before as Record<string,unknown>)[key],value));
  return false;
}
/** Inline only. Omitted exact duplicates retain an index; no unreadable external log references masquerade as evidence. */
export function prepareProposalEvidence<T extends RepairItem>(items:T[], strict=false):T[] {
  return items.map(item=>{
    const context=structuredClone(item.context??{});
    const diagnoses=(Array.isArray(context.diagnoses)?context.diagnoses:[]) as Array<Record<string,any>>;
    const sourceIndex:Array<Record<string,unknown>>=[], examples:Array<Record<string,unknown>>=[];
    const seen=new Map<string,number>();let missing=false,clipped=false;
    const redacted=(value:unknown)=>{
      const safe=redactEvidenceValue(value);
      clipped=collectionClipped(value,safe)||clipped;
      return safe;
    };
    for(const diagnosis of diagnoses){
      const evidence=(Array.isArray(diagnosis.evidence)?diagnosis.evidence:[]) as Array<Record<string,any>>;
      const refs:Array<Record<string,any>>=evidence.map(event=>{
        const actual=event.payload==null?null:digestRepairJson(event.payload);
        const absent=event.missing||!actual||event.payloadDigest&&event.payloadDigest!==actual;
        if(absent)missing=true;
        return {...event,...(absent?{missing:true,payload:null}:{payload: redacted(event.payload)})};
      });
      // Preserve differing versions/input boundaries/unknowns; version-unknown cases are never deduplicated across runs.
      const key=digestRepairJson({node:diagnosis.nodeId??null,mode:diagnosis.failureMode??null,reason:diagnosis.reasoning??null,
        version:diagnosis.observedDeployNumber??diagnosis.flowId??null,analysisContext:diagnosis.analysisContext??null,
        evidence:refs.map(e=>({payload:e.payload,eventType:e.eventType??null,nodeId:e.nodeId??null,missing:!!e.missing,occurredAtMs:e.occurredAtMs??null}))});
      let representative=seen.get(key);
      if(representative===undefined){representative=examples.length;seen.set(key,representative);examples.push({...diagnosis,evidence:refs});}
      sourceIndex.push({analysisId:diagnosis.analysisId??null,diagnosisId:diagnosis.diagnosisId??null,flowId:diagnosis.flowId??null,
        observedDeployNumber:diagnosis.observedDeployNumber??null,representative,
        evidence:refs.map(e=>({eventId:e.eventId??null,payloadDigest:e.payloadDigest??null,missing:!!e.missing}))});
    }
    const safe=redacted({...context,diagnoses:examples}) as Record<string,unknown>;
    clipped=clipped||canonicalRepairJson(safe).includes('[TRUNCATED');
    if(strict&&(missing||clipped))throw new RepairBatchError('INVALID_INPUT','关键证据缺失、摘要被裁剪或原文校验失败，请补充证据后再整合',{reasonCode:'EVIDENCE_INCOMPLETE'});
    return {...item,context:{...safe,evidencePacket:{schemaVersion:'repair-evidence-packet/v1',sourceIndex,
      originalDiagnoses:diagnoses.length,representatives:examples.length,missingEvidence:missing,clipped,
      redaction:'credentials-and-PII-redacted; exact duplicate evidence indexed; no unbounded logs or external-only payloads',
      digest:digestRepairJson(safe)}}};
  });
}
