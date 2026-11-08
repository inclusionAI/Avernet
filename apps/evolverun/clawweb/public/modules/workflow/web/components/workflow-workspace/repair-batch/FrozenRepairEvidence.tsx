import { useState } from 'react'
import type { RepairItem, RepairRevision } from '../../../../server/contracts/repair-batch'
import { repairBatches } from '../../../api/repair-batches'
import { JsonDetails } from './repair-view'
export default function FrozenRepairEvidence({revision,item}:{revision:RepairRevision;item:RepairItem}){
 const [original,setOriginal]=useState<RepairItem|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false)
 if(!revision.input.materials)return <JsonDetails title="原始建议与证据" value={item}/>
 return <details className="mt-2 text-xs" onToggle={async event=>{
  if(!event.currentTarget.open||original||loading)return
  setLoading(true);setError('')
  try{setOriginal(await repairBatches.material(revision.taskId,revision.revision,item.itemId))}
  catch{setError('冻结材料读取失败；未使用实时数据替代。收起后可重新展开读取。')}
  finally{setLoading(false)}
 }}><summary className="cursor-pointer text-muted-foreground">原始建议与证据</summary>
  {loading&&<p role="status">正在读取本版完整冻结材料…</p>}
  {error&&<p role="alert" className="text-destructive">{error}</p>}
  {original&&<pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-muted/50 p-3 text-[11px]">{JSON.stringify(original,null,2)}</pre>}
 </details>
}
