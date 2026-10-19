import { render, screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { RepairTaskDetail } from '../../../../../server/contracts/repair-workbench'
const api=vi.hoisted(()=>({diff:vi.fn(),trials:vi.fn(),verification:vi.fn(),publish:vi.fn(),deploymentTargets:vi.fn(),reconcileDeployment:vi.fn(),resumeDeployment:vi.fn()}))
vi.mock('../../../../api/repair-batches',()=>({repairBatches:api}))
import RepairTaskPanel from '../RepairTaskPanel'
function fixture():RepairTaskDetail {
 const revision={workflowId:'wf',taskId:'task-ui',revision:1,phase:'review',stateVersion:1,
  input:{schemaVersion:'workflow-repair/v2',taskId:'task-ui',revision:1,
   baseline:{workflowId:'wf',packId:'pack',activeDeployNumber:159,releaseRevision:1,repoId:'repo',specPath:'workflow.yaml',specDigest:'a'.repeat(64),packCommit:'b'.repeat(40),packDigest:'c'.repeat(40)},
   items:[{itemId:'source',groupKey:'group',proposalKey:'proposal',contentRevision:1,previousItemId:null,proposal:{summary:'校验输入并限制输出'},instruction:'',sources:[],context:{nodeId:'query'}}],
   instructions:'',feedback:'',parentCandidateCommit:null,previousReportRef:null,taskBranch:'repair/task-ui',excludedSources:{count:0,digest:'d'.repeat(64)}},
  draft:{candidateCommit:'e'.repeat(40),summary:'workflow repair candidate: 1/1 items changed',itemResults:[{itemId:'source',status:'changed',reason:'增加输入与输出约束'}]},
  checks:{static:{status:'passed'},mock:{status:'not_covered'},uncoveredChecks:['external_resources']},error:null}
 return {workflowId:'wf',taskId:'task-ui',latestAttempt:revision,latestSuccessful:revision,revisions:[revision],capabilities:{generation:true,diff:true,publication:true,reason:null},release:{approval:null,deployments:[]},execution:{status:'succeeded',jobId:'job',attempt:1,executionId:'exec',errorCode:null,rawStatus:'success'}} as unknown as RepairTaskDetail
}
const props=()=>({canEdit:true,onFeedback:vi.fn(),onCancel:vi.fn(),onRetryDispatch:vi.fn(),onRetryGeneration:vi.fn(),onRefresh:vi.fn(),dispatchError:'',busy:false})
function mount(detail=fixture(),extra={}){return render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><RepairTaskPanel detail={detail} {...props()} {...extra}/></QueryClientProvider>)}
beforeEach(()=>{sessionStorage.clear();vi.clearAllMocks();api.diff.mockResolvedValue({files:[],truncated:false});api.trials.mockResolvedValue({configured:false,trials:[]});api.verification.mockResolvedValue({evidence:{deployNumber:160,total:0,excludedUnknownVersions:0,samples:[],automatic:[]},conclusions:[]})})
it('switches actual content without generating, trialing or publishing and supports arrow-key focus',async()=>{
 const user=userEvent.setup(),onFeedback=vi.fn();mount(fixture(),{onFeedback});
 const tabs=await screen.findByRole('tablist',{name:'任务内容'});
 expect(within(tabs).getByRole('tab',{name:'候选与发布'})).toHaveAttribute('aria-selected','true');
 expect(screen.queryByRole('heading',{name:'本版处理范围'})).not.toBeInTheDocument();
 await user.click(within(tabs).getByRole('tab',{name:'问题与方案'}));
 expect(screen.getByRole('heading',{name:'本版处理范围'})).toBeVisible();
 expect(screen.queryByRole('heading',{name:'候选修改'})).not.toBeInTheDocument();
 await user.keyboard('{End}');expect(within(tabs).getByRole('tab',{name:'效果观察'})).toHaveFocus();
 expect(screen.getByText('此候选尚未发布，暂无对应部署的观察证据。')).toBeVisible();
 await user.click(screen.getByRole('button',{name:'查看候选与发布'}));
 expect(screen.getByRole('heading',{name:'候选修改'})).toBeVisible();
 expect(onFeedback).not.toHaveBeenCalled();expect(api.publish).not.toHaveBeenCalled();expect(api.deploymentTargets).not.toHaveBeenCalled();
});
it('keeps the published v1 identity prominent when the latest automatic v2 needs no change',async()=>{
 const detail=fixture();detail.latestSuccessful!.phase='published';
 detail.latestSuccessful!.input.automation={roundId:'daily'} as never;
 const latest={...detail.latestAttempt,revision:2,phase:'no_change' as const,draft:null};
 detail.latestAttempt=latest;detail.revisions.push(latest);
 detail.release!.deployments=[{id:'deploy',revision:1,status:'succeeded',baselineDeployNumber:159,receipt:{deployNumber:160}} as never];
 mount(detail);
 const header=screen.getByRole('region',{name:'当前候选'});
 expect(within(header).getByText(/当前查看 v1/)).toBeVisible();expect(within(header).getByText(/部署 #160/)).toBeVisible();
 expect(screen.getByText(/每日自动优化/)).toBeVisible();expect(screen.queryByText(/自动生成草稿 · 等待人工审阅/)).not.toBeInTheDocument();
 expect(screen.getAllByRole('combobox',{name:'查看修复版本'})).toHaveLength(1);
 expect(screen.queryByRole('contentinfo',{name:'当前修复步骤与下一步'})).not.toBeInTheDocument();
 expect(await screen.findByText('暂无新版本运行样本，暂不能判断效果。')).toBeVisible();
});
it('keeps latest recovery separate from a selected historical candidate and shows each version error',async()=>{
 const detail=fixture();const latest={...detail.latestAttempt,revision:2,phase:'failed' as const,draft:null,error:{code:'AIS_FAILED',message:'v2 failure'}};
 detail.latestAttempt=latest;detail.revisions.push(latest);mount(detail);
 expect(screen.getByRole('region',{name:'当前候选'})).toHaveTextContent('当前查看 v1');
 expect(screen.getByText(/最新尝试 v2/)).toBeVisible();
 await userEvent.selectOptions(screen.getByRole('combobox',{name:'查看修复版本'}),'2');
 expect(screen.getByText('所选版本 v2：生成失败')).toBeVisible();
 expect(screen.queryByRole('button',{name:'发布此版本'})).not.toBeInTheDocument();
 expect(api.publish).not.toHaveBeenCalled();
});
it('explains an intermediate automatic no-change batch without claiming the round is ready',async()=>{
 const detail=fixture();detail.latestAttempt.phase='no_change';detail.latestAttempt.input.automation={roundId:'daily'} as never;
 detail.automaticProgress={completedBatches:1,totalBatches:3,changedItems:1,publishable:false};mount(detail);
 expect(screen.getByText('本批无需新增修改，整轮仍在核对。')).toBeVisible();
 expect(screen.queryByText('本轮无需重新发布')).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'发布此版本'})).not.toBeInTheDocument();
});
it('summarizes real checks and outcomes before folded reports and retains diff retry',async()=>{
 api.diff.mockRejectedValueOnce(new Error('controlled diff unavailable')).mockResolvedValue({files:[],truncated:false});mount();
 expect(screen.getByText('已修改 1 项 · 无需修改 0 项 · 未解决 0 项')).toBeVisible();
 expect(screen.getByText(/静态检查：通过/)).toBeVisible();expect(screen.getByText(/Mock 检查：未覆盖/)).toBeVisible();
 expect(screen.queryByText('workflow repair candidate: 1/1 items changed')).not.toBeVisible();
 await userEvent.click(await screen.findByRole('button',{name:'重试差异'}));
 await waitFor(()=>expect(api.diff).toHaveBeenCalledTimes(2));
});
it('persists the inspection tab across a close and reopen without opening the scope editor',async()=>{
 const first=mount();await userEvent.click(screen.getByRole('tab',{name:'问题与方案'}));first.unmount();mount();
 expect(screen.getByRole('tab',{name:'问题与方案'})).toHaveAttribute('aria-selected','true');
 expect(screen.getByRole('heading',{name:'本版处理范围'})).toBeVisible();
});
it('allows read-only navigation but disables publish and source editing',async()=>{
 mount(fixture(),{canEdit:false});expect(await screen.findByRole('button',{name:'发布此版本'})).toBeDisabled();
 await userEvent.click(screen.getByRole('tab',{name:'问题与方案'}));
 expect(screen.getByText('当前只有查看权限。')).toBeVisible();expect(screen.queryByRole('button',{name:'调整范围与要求'})).not.toBeInTheDocument();
 await userEvent.click(screen.getByRole('tab',{name:'效果观察'}));expect(screen.getByRole('button',{name:'查看候选与发布'})).toBeEnabled();
});
it('retains the same publication request after a lost response and inspection tab switches',async()=>{
 api.deploymentTargets.mockResolvedValue({targets:[{id:'workflow:pre:wf',scope:'workflow',label:'工作流 wf',environment:'pre'}]});
 api.publish.mockRejectedValueOnce(new Error('controlled lost response')).mockResolvedValue({status:'running'});
 mount();const user=userEvent.setup();
 await user.click(await screen.findByRole('button',{name:'发布此版本'}));
 await screen.findByText('工作流 wf');await user.click(screen.getByRole('checkbox'));
 await user.click(screen.getByRole('button',{name:'确认发布'}));await screen.findByRole('alert');
 const request=api.publish.mock.calls[0];
 await user.click(screen.getByRole('tab',{name:'问题与方案'}));await user.click(screen.getByRole('tab',{name:'效果观察'}));await user.click(screen.getByRole('tab',{name:'候选与发布'}));
 expect(screen.getByRole('checkbox')).toBeChecked();expect(api.publish).toHaveBeenCalledTimes(1);
 await user.click(screen.getByRole('button',{name:'确认发布'}));await waitFor(()=>expect(api.publish).toHaveBeenCalledTimes(2));
 expect(api.publish.mock.calls[1]).toEqual(request);
});
it('does not attach a late publication error to a different selected revision',async()=>{
 const detail=fixture();const old={...detail.latestAttempt,revision:0};detail.revisions.push(old);
 api.deploymentTargets.mockResolvedValue({targets:[{id:'workflow:pre:wf',scope:'workflow',label:'工作流 wf',environment:'pre'}]});
 let fail!:(error:Error)=>void;api.publish.mockImplementationOnce(()=>new Promise((_resolve,reject)=>{fail=reject}));
 mount(detail);const user=userEvent.setup();await user.click(await screen.findByRole('button',{name:'发布此版本'}));await screen.findByText('工作流 wf');await user.click(screen.getByRole('checkbox'));await user.click(screen.getByRole('button',{name:'确认发布'}));
 await user.selectOptions(screen.getByRole('combobox',{name:'查看修复版本'}),'0');
 fail(new Error('error belongs only to v1'));await waitFor(()=>expect(screen.queryByText('提交中…')).not.toBeInTheDocument());
 expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
it('shows provided diagnostic impact and folded cause certainty without inventing recent counts',async()=>{
 const detail=fixture();detail.latestAttempt.input.items[0].context={nodeId:'query',diagnoses:[{severity:'high'}]};
 detail.latestAttempt.input.integration={schemaVersion:'workflow-proposal-plan/v1',baselineSpecDigest:detail.latestAttempt.input.baseline.specDigest,plans:[{id:'plan',originalItemIds:['source'],goals:['校验输入并限制输出'],relation:'complementary',causes:[{title:'输入缺失',certainty:'hypothesis',itemIds:['source'],evidenceIds:[]}],unknowns:['缺少边界样本']}]};
 mount(detail);await userEvent.click(screen.getByRole('tab',{name:'问题与方案'}));
 expect(screen.getByText('诊断影响：高')).toBeVisible();expect(screen.getByText(/近期影响次数未知/)).toBeVisible();
 await userEvent.click(screen.getByText('原因与依据',{selector:'summary'}));
 expect(screen.getByText('推测：输入缺失')).toBeVisible();expect(screen.getByText('待确认：缺少边界样本')).toBeVisible();
});

it('counts distinct frozen runs from suggestion and diagnosis context alongside direct sources',async()=>{
 const detail=fixture(),item=detail.latestAttempt.input.items[0];
 item.sources=[{kind:'suggestion',suggestionId:'suggestion'}] as never;
 item.context={diagnoses:[{flowId:'run-a',flowIds:['run-a','run-b','',null]}],suggestions:[{runIds:['run-b','run-c',42]},{impactRunIds:['run-d','']},null]};
 detail.latestAttempt.input.items.push({...item,itemId:'direct',sources:[{kind:'diagnosis_candidate',flowId:'run-d'}] as never,context:{}});
 mount(detail);await userEvent.click(screen.getByRole('tab',{name:'问题与方案'}));
 expect(screen.getByText(/依据覆盖 4 个运行/)).toBeVisible();expect(screen.queryByText(/来源运行数未知/)).not.toBeInTheDocument();
});
