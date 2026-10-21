import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import type { RepairRevision } from '../../../../../server/contracts/repair-batch'
import RepairTaskScope from '../RepairTaskScope'

const api = vi.hoisted(() => ({ material: vi.fn() }))
vi.mock('../../../../api/repair-batches', () => ({ repairBatches: api }))

function revision(): RepairRevision {
 return {
  workflowId:'wf',taskId:'task',revision:4,phase:'drafting',stateVersion:0,requestId:'request',requestDigest:'a'.repeat(64),
  input:{schemaVersion:'workflow-repair/v2',taskId:'task',revision:4,
   baseline:{workflowId:'wf',packId:'pack',activeDeployNumber:162,releaseRevision:1,repoId:'repo',specPath:'workflow.yaml',specDigest:'b'.repeat(64),packCommit:'c'.repeat(40),packDigest:'d'.repeat(40)},
   items:[
    {itemId:'a'.repeat(64),groupKey:'report',proposalKey:'output',contentRevision:1,previousItemId:null,proposal:{summary:'调整 report 输出契约，接受对象输出',operations:[{nodeId:'report'}]},instruction:'完整输出要求',sources:[],context:{nodeId:'report'}},
    {itemId:'b'.repeat(64),groupKey:'report',proposalKey:'context',contentRevision:1,previousItemId:null,proposal:{summary:'明确 report 引用 search 的输出',operations:[{nodeId:'report'},{nodeId:'search'}]},instruction:'完整上下文要求',sources:[],context:{diagnoses:[{nodeId:'search'}]}},
   ],
   integration:{schemaVersion:'workflow-proposal-plan/v1',baselineSpecDigest:'b'.repeat(64),plans:[{id:'initial-group',originalItemIds:['a'.repeat(64),'b'.repeat(64)],goals:[`综合核对原始建议 ${'a'.repeat(64)} 的完整目标；合并重复目标，保留互补约束，无法兼容的目标报告未解决，不扩大用户选择。`],relation:'complementary'}]},
   excludedSources:{count:0,digest:'e'.repeat(64)},instructions:'',feedback:'',parentCandidateCommit:null,previousReportRef:null,taskBranch:'repair/task'},
  draft:null,checks:null,candidateDigest:null,checksDigest:null,error:null,createdAtMs:1,updatedAtMs:1,
 }
}

it('shows the concrete selected goals and related nodes while folding initial execution rules and IDs', async () => {
 const value=revision(),original=JSON.stringify(value)
 render(<RepairTaskScope revision={value}/>);
 const group=screen.getByRole('article')
 expect(within(group).getByRole('heading',{name:'目标组 1 · 2 条建议'})).toBeVisible()
 expect(within(group).getByText('相关节点：report、search')).toBeVisible()
 for(const title of ['调整 report 输出契约，接受对象输出','明确 report 引用 search 的输出'])expect(within(group).getByText(title)).toBeVisible()
 expect(screen.queryByRole('heading',{name:/方案/})).not.toBeInTheDocument()
 const technical=screen.getByText('分组技术详情',{selector:'summary'})
 expect(technical.closest('details')).not.toHaveAttribute('open')
 await userEvent.click(technical)
 expect(within(technical.closest('details')!).getByText(/综合核对原始建议/)).toBeVisible()
 expect(within(technical.closest('details')!).getByText(/initial-group/)).toBeVisible()
 expect(screen.getByText('来源建议与依据',{selector:'summary'})).toBeVisible()
 expect(JSON.stringify(value)).toBe(original)
})

it('keeps meaningful aggregation goals in details and uses instructions when a source summary is blank', async () => {
 const value=revision();value.input.items[0].proposal={summary:'  '};value.input.items[0].context={}
 value.input.items.splice(1)
 value.input.integration!.plans[0]={id:'automatic-group',originalItemIds:['a'.repeat(64)],goals:['保证报告输出可被下游解析'],relation:'duplicate'}
 render(<RepairTaskScope revision={value}/>);
 expect(screen.getByText('完整输出要求')).toBeVisible()
 expect(screen.getByText('相关节点：未知')).toBeVisible()
 await userEvent.click(screen.getByText('分组技术详情',{selector:'summary'}))
 expect(screen.getByText(/保证报告输出可被下游解析/)).toBeVisible()
})

it('includes known nodes from legacy text suggestions without operations or matching diagnoses', () => {
 const value=revision()
 for(const item of value.input.items)item.proposal=null
 value.input.items[0].context={suggestions:[{nodeId:'report'},{nodeId:'search'},null,{nodeId:''},{nodeId:42}]}
 value.input.items[1].context={suggestions:[{nodeId:'report'}]}
 render(<RepairTaskScope revision={value}/>);
 expect(screen.getByText('相关节点：report、search')).toBeVisible()
 expect(screen.queryByText('相关节点：未知')).not.toBeInTheDocument()
})

it('displays frozen core summaries without downloading optional originals until evidence is expanded', async () => {
 const value=revision();value.input.materials={schemaVersion:'repair-frozen-materials/v1',recordId:'materials',sha256:'f'.repeat(64),bytes:6973030}
 api.material.mockReset();api.material.mockResolvedValue({...value.input.items[0],instruction:'完整冻结证据'})
 render(<RepairTaskScope revision={value}/>);
 expect(screen.getByText('调整 report 输出契约，接受对象输出')).toBeVisible()
 expect(api.material).not.toHaveBeenCalled()
 await userEvent.click(screen.getByText('来源建议与依据',{selector:'summary'}))
 await userEvent.click(screen.getAllByText('原始建议与证据',{selector:'summary'})[0])
 expect(await screen.findByText(/完整冻结证据/)).toBeVisible()
 expect(api.material).toHaveBeenCalledExactlyOnceWith('task',4,'a'.repeat(64))
})
