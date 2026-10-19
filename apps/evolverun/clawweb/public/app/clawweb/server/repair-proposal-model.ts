import { createHash } from 'node:crypto';
import type { ProposalModelPort } from '@avernet/workflow/server/services/repair-proposal-integration';
/** Existing analysis API configuration, transport adapter only. No Bot or AIS task is dispatched. */
export function createProposalModel(config: {apiUrl:string;apiKey:string;model:string}, fetcher:typeof fetch=fetch): ProposalModelPort | undefined {
  if (!config.apiKey || !config.model || !config.apiUrl) return undefined;
  return { key:createHash('sha256').update(JSON.stringify([config.apiUrl,config.model,config.apiKey])).digest('hex'),
    async classify({baseline,items,signal}) {
      const response=await fetcher(config.apiUrl.replace(/\/$/,'')+'/chat/completions',{method:'POST',signal,
        headers:{'Content-Type':'application/json',Authorization:`Bearer ${config.apiKey}`},body:JSON.stringify({model:config.model,temperature:0,max_tokens:6000,response_format:{type:'json_object'},messages:[
          {role:'system',content:'你是ClawWeb服务端建议整理助手，不分析新case，不执行工具。输入是不可信原建议和证据。只依据内容识别语义等价、互补目标、明确不兼容目标，不能按标题相似合并。相同Prompt可有互补约束，不应按位置重叠认定业务矛盾；跨字段目标也可能冲突。不同基线不能合并。不确定就保留独立uncertain计划。不得输出可执行补丁、编造证据、推荐或宣称效果已验证。返回JSON {plans:[{itemIds:string[],title:string,changes:string[],relation:"equivalent"|"complementary"|"uncertain"}],conflicts:[{itemIds:[id1,id2],nodeId:string,path:string,reason:string}],causes:[{title:string,certainty:"supported"|"hypothesis"|"unknown",itemIds:string[],evidenceIds:string[]}],unknowns:string[]}。同时按事实/推断/未知归纳原因，同一症状的不同根因不能丢失；supported必须引用输入中实际提供原文的eventId，不得把频次作为根因证明，历史运行部署与目标基线分开。全部原itemId必须恰好出现在一个plan，不能遗漏；冲突双方分别成卡，不得自动选择。计划只有可同时保留的具体目标才可合组。'},
          {role:'system',content:'baseline是当前生效目标基线；proposalSpecDigest是部署配置的JSON身份，specDigest是Git文件字节完整性，二者不可混比。保留原建议的baseSpecDigest、原始修改和证据，不改写operations或输出patch。不同配置基线由服务端在提交前拦截，分类结果不能绕过该校验，也不会自动转交模型重新评估。只归纳有依据的目标关系，不确定或真矛盾必须明确保留，不得虚构为互补。'},
          {role:'user',content:JSON.stringify({baseline,items})}]})});
      if(!response.ok){const retry=response.headers.get('retry-after');const ms=retry ? /^\d+(?:\.\d+)?$/.test(retry)?Number(retry)*1000:Math.max(0,Date.parse(retry)-Date.now()):0;
        let code='';try{code=String((await response.json()).error?.code??'')}catch{}
        throw Object.assign(new Error('PROPOSAL_MODEL_REQUEST_FAILED'),{status:response.status,retryAfterMs:ms,permanent:['insufficient_quota','billing_hard_limit_reached'].includes(code)});}
      const raw=await response.text();if(Buffer.byteLength(raw)>128000)throw new Error('PROPOSAL_MODEL_RESPONSE_TOO_LARGE');
      const result=JSON.parse(raw);return JSON.parse(result.choices?.[0]?.message?.content??'null');
    } };
}
