import type { RepairRevision } from '../../../../server/contracts/repair-batch'
const stages:Record<string,string>={parse_model_output:'模型结果解析',model_execution:'调用模型',prepare_model_input:'准备模型输入',generating:'生成修改',checking:'检查候选',pushing:'保存候选'}
const parserReasons:Record<string,string>={
 'Expecting property name enclosed in double quotes':'JSON 属性名必须使用双引号',
 'Expecting value':'JSON 中缺少合法的值',
 "Expecting ',' delimiter":'JSON 中缺少逗号分隔符',
 "Expecting ':' delimiter":'JSON 属性名后缺少冒号',
 'Unterminated string starting at':'JSON 字符串没有完整结束',
}
export default function RepairFailureDetails({revision,executionId}:{revision:RepairRevision;executionId?:string|null}){
 if(revision.phase!=='failed'||!revision.error)return null
 const error=revision.error
 const details=error.details&&typeof error.details==='object'?error.details as Record<string,unknown>:{}
 const ids=Array.isArray(details.itemIds)?details.itemIds.filter((value):value is string=>typeof value==='string'):[]
 const items=revision.input.items.filter(item=>ids.includes(item.itemId))
 const stage=String(error.stage??details.stage??'')
 const reason=typeof details.parserMessage==='string'?(parserReasons[details.parserMessage]??details.parserMessage):String(error.message??'执行失败，尚未回传详细原因')
 return <section role="alert" aria-label="生成失败详情" className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm">
  <h3 className="font-semibold">本次生成失败 · {stages[stage]??'执行阶段待确认'}</h3>
  {items.length>0&&<div className="mt-2"><p>{items.length>1?`涉及建议组：${items.length} 条建议`:`涉及建议：第 ${details.itemIndex??'—'}/${details.itemCount??revision.input.items.length} 条`}</p>
   <ul className="mt-1 list-disc pl-5">{items.map(item=><li key={item.itemId}>{String(item.proposal?.summary||item.instruction||item.itemId)}</li>)}</ul></div>}
  <p className="mt-2 break-words">具体原因：{reason}</p>
  {typeof details.line==='number'&&typeof details.column==='number'&&<p>错误位置：第 {details.line} 行，第 {details.column} 列{typeof details.position==='number'?`（字符位置 ${details.position}）`:''}</p>}
  {typeof details.exitCode==='number'&&<p>模型进程退出码：{details.exitCode}</p>}
  {typeof details.timeoutSeconds==='number'&&<p>等待模型超过 {details.timeoutSeconds} 秒</p>}
  {typeof details.responseBytes==='number'&&<p>模型返回：{details.responseBytes} 字节</p>}
  <p className="mt-2 text-muted-foreground">{stage==='parse_model_output'?'模型返回内容未通过格式校验，本次没有生成可发布候选。可以沿用原要求重试；若持续失败，请提供下方执行编号排查，无需下载原始文件。':'请根据错误原因处理；若原因不完整，请提供下方执行编号排查，先核对原任务状态。'}</p>
  <p className="mt-2 break-all text-xs text-muted-foreground">错误码：{String(details.reasonCode??error.code??'UNKNOWN')} · 版本：v{revision.revision}{executionId?` · 执行编号：${executionId}`:''}</p>
 </section>
}
