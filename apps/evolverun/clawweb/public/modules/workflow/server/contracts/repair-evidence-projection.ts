import { canonicalRepairJson, digestRepairJson,repairEvidenceFacts,repairEvidenceFlows, type RepairItem } from './repair-batch.js';
const records=(value:unknown):Record<string,any>[]=>Array.isArray(value)?value.filter(row=>row&&typeof row==='object'):[];
const excerpt=(value:unknown,limit:number)=>typeof value==='string'&&value.length>limit?value.slice(0,limit)+'…':value;
function payload(value:unknown){
 const json=canonicalRepairJson(value);
 return Buffer.byteLength(json)<=1200?value:{keyErrors:typeof value==='object'&&value?Object.fromEntries(['error','errorText','message','code','status'].filter(key=>key in value).map(key=>[key,excerpt(typeof (value as Record<string,unknown>)[key]==='object'?canonicalRepairJson((value as Record<string,unknown>)[key]):(value as Record<string,unknown>)[key],600)])):{},excerpt:Buffer.from(json).subarray(0,1000).toString('utf8'),truncated:true,bytes:Buffer.byteLength(json),sha256:digestRepairJson(value)};
}
/** A bounded reading aid, never a replacement for the separately retained originals. */
export function projectRepairEvidence<T extends RepairItem>(item:T,forAggregation=false):T{
 const context=item.context??{},diagnoses=records(context.diagnoses),sources=item.sources;
 const projected:Record<string,unknown>={};
 for(const key of ['signature','nodeId','failureMode','sourceSnapshot','targetSnapshot','referenceOnly'])if(context[key]!==undefined)projected[key]=context[key];
 const flows=repairEvidenceFlows(item),facts=repairEvidenceFacts(item);
 projected.evidenceSummary={diagnosisCount:diagnoses.length,sourceCount:sources.length,
  ...(!forAggregation?{sourceRefsSha256:digestRepairJson(sources)}:{}),
  flowCount:flows.length,flowIds:forAggregation?flows:flows.slice(0,3),severity:facts.severity,eventCount:facts.eventIds.length,eventIds:forAggregation?facts.eventIds:facts.eventIds.slice(0,3),
  excerpted:true,originals:forAggregation?'Original suggestions retain complete reports; this aggregation request contains excerpts only, which do not prove resolution.':'Frozen materials retain complete source references, reports and evidence; inline identities are samples, not the full scope. Excerpts do not prove resolution.'};
 projected.diagnoses=diagnoses.slice(0,2).map(row=>{
  const result:Record<string,unknown>={};
  for(const key of ['analysisId','diagnosisId','flowId','nodeId','failureMode','severity','completedAtMs'])if(row[key]!==undefined)result[key]=row[key];
  if(row.reasoning!==undefined)result.reasoning=excerpt(row.reasoning,400);
  const analysis=row.analysisContext;
  if(analysis&&typeof analysis==='object'){
   const kept:Record<string,unknown>={};
   for(const key of ['facts','inferences','unknowns'])if(Array.isArray(analysis[key]))kept[key]=analysis[key].slice(0,3).map((value:unknown)=>excerpt(typeof value==='string'?value:canonicalRepairJson(value),240));
   if(analysis.evidenceStatus!==undefined)kept.evidenceStatus=excerpt(analysis.evidenceStatus,200);
   result.analysisContext=kept;
  }
  result.evidence=records(row.evidence).slice(0,1).map(event=>{
   const kept:Record<string,unknown>={};
   for(const key of ['eventId','flowId','nodeId','eventType','payloadDigest','occurredAtMs','missing'])if(event[key]!==undefined)kept[key]=event[key];
   const missing=event.missing||event.payload==null||forAggregation&&event.payloadDigest&&digestRepairJson(event.payload)!==event.payloadDigest;
   return {...kept,...(missing?{missing:true}:{}),payload:missing?null:payload(event.payload)};
  });
  return result;
 });
 return {...item,sources:sources.slice(0,3),context:projected};
}
