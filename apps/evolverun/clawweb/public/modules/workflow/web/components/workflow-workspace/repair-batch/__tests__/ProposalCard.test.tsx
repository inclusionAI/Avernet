import { fireEvent,render,screen } from '@testing-library/react';
import { expect,it,vi } from 'vitest';
import IssueRepairPreview from '../IssueRepairPreview';
import { expandProposalCards,proposalChecked,proposalConflicts,proposalPending } from '../../../../../server/contracts/repair-proposal';
import type { RepairInboxItem } from '../../../../../server/contracts/repair-workbench';
const api=vi.hoisted(()=>({item:vi.fn()}));
vi.mock('../../../../api/repair-batches',()=>({repairBatches:api}));
const raw=(id:string,value:string):RepairInboxItem=>({itemId:id,workflowId:'wf',groupKey:'g',proposalKey:id,contentRevision:1,previousItemId:null,
 proposal:{summary:`原建议${id}`,operations:[{nodeId:'query',path:'/prompt',op:'replace',value}]},instruction:`原始要求${id}`,sources:[],context:{signature:'issue'},
 state:'pending',stateVersion:0,sourceAvailable:true,updatedAtMs:1,episodeKey:'initial',activeTaskId:null,activeRevision:null,disposition:null});
const originals=[raw('a','validate input'),raw('b','use exec'),raw('c','limit raw data')];
const card={...originals[0],aggregation:{id:'plan',fingerprint:'fp',itemIds:['a','b','c'],title:'完善查询输入与输出',changes:['校验输入','明确工具','限制输出'],relation:'complementary',status:'ready',flowIds:['run-1','run-2'],originals,conflicts:[]}} as RepairInboxItem;
it('shows one plan/selection and keeps all original text and evidence folded',()=>{
 const toggle=vi.fn();render(<IssueRepairPreview items={[card]} total={1} selected={[]} canEdit limit={100} onToggle={toggle} onDetail={vi.fn()} onMore={vi.fn()}/>);
 expect(screen.getAllByRole('checkbox')).toHaveLength(1);expect(screen.getByText('完善查询输入与输出')).toBeVisible();
 expect(screen.getByText(/校验输入/)).toBeVisible();expect(screen.getByText(/依据覆盖 2 个运行/)).toBeVisible();
 expect(screen.getByText('原始建议与证据（3）').closest('details')).not.toHaveAttribute('open');
 fireEvent.click(screen.getByText('原始建议与证据（3）'));expect(screen.getByText('原始要求c')).toBeVisible();
 fireEvent.click(screen.getByRole('checkbox'));expect(toggle).toHaveBeenCalledWith('a');
 const selected=expandProposalCards([card]);expect(selected.map(i=>i.itemId)).toEqual(['a','b','c']);
 expect(proposalChecked(card,['a'])).toBe(false);expect(proposalPending([selected[0]])).toBe(true);expect(proposalChecked(card,['a','b','c'])).toBe(true);
 expect(proposalConflicts(selected)).toEqual([]);expect(proposalPending(selected)).toBe(false);
});
it('distinguishes queued/failed aggregation from business conflicts',()=>{
 const queued={...card,aggregation:{...card.aggregation!,status:'retryable' as const}};
 render(<IssueRepairPreview items={[queued]} total={1} selected={[]} canEdit limit={100} onToggle={vi.fn()} onDetail={vi.fn()} onMore={vi.fn()}/>);
 expect(screen.getByText('整理待重试')).toBeVisible();expect(proposalConflicts(expandProposalCards([queued]))).toEqual([]);
 expect(proposalPending(expandProposalCards([queued]))).toBe(true);
});
it('loads full original evidence explicitly while retaining one complete plan selection',async()=>{
 const summary={...originals[0],proposal:{summary:'原建议a'},instruction:'摘要',sources:[],context:{},readSummary:{sourceCount:110,runCount:110,diagnosisCount:110,originalBytes:1110714,operations:[],conflictsComplete:true}};
 const compact={...card,aggregation:{...card.aggregation!,originals:[summary,...originals.slice(1)]}};
 api.item.mockResolvedValue({...originals[0],context:{proof:'full-original-evidence'}});
 const view=render(<IssueRepairPreview items={[compact]} total={1} selected={[]} canEdit limit={100} onToggle={vi.fn()} onDetail={vi.fn()} onMore={vi.fn()}/>);
 expect(screen.queryByText(/full-original-evidence/)).not.toBeInTheDocument();
 fireEvent.click(screen.getByText('原始建议与证据（3）'));
 expect(screen.getByText(/110 条来源/)).toBeVisible();
 fireEvent.click(screen.getByRole('button',{name:'读取完整修改、来源与证据'}));
 expect(await screen.findByText(/full-original-evidence/)).toBeVisible();
 expect(api.item).toHaveBeenCalledWith('wf','a',true);
 expect(expandProposalCards([compact]).map(row=>row.itemId)).toEqual(['a','b','c']);
 view.rerender(<IssueRepairPreview items={[{...compact,aggregation:{...compact.aggregation!,fingerprint:'updated-evidence'}}]} total={1} selected={[]} canEdit limit={100} onToggle={vi.fn()} onDetail={vi.fn()} onMore={vi.fn()}/>);
 expect(screen.queryByText(/full-original-evidence/)).not.toBeInTheDocument();
});
