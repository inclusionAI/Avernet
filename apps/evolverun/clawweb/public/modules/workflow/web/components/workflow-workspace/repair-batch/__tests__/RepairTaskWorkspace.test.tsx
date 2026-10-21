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
it('identifies global assessment failures and preserves the selected problem and execution reference',()=>{
 const detail=fixture();detail.latestAttempt.phase='failed';detail.latestAttempt.draft=null;detail.latestSuccessful=null;
 detail.latestAttempt.error={code:'CANDIDATE_WRITE_FAILED',message:'全局修复评估失败：计划缺少原始问题',stage:'planning',details:{reasonCode:'REPAIR_STRATEGY_INVALID',itemIds:['source'],itemCount:1}};
 mount(detail);
 const failure=screen.getByRole('alert',{name:'生成失败详情'});
 expect(failure).toHaveTextContent('本次生成失败 · 全局修复评估');
 expect(failure).toHaveTextContent('校验输入并限制输出');
 expect(failure).toHaveTextContent('exec');
});
it('shows the failed stage, original goal, exact parse location and next action without downloading diagnostics',()=>{
 const detail=fixture();detail.latestAttempt.phase='failed';detail.latestAttempt.draft=null;detail.latestSuccessful=null;
 detail.latestAttempt.error={code:'CANDIDATE_WRITE_FAILED',message:'生成结果解析失败',stage:'parse_model_output',details:{reasonCode:'MODEL_OUTPUT_INVALID_JSON',itemIds:['source'],itemIndex:1,itemCount:1,line:1,column:2,parserMessage:'Expecting property name enclosed in double quotes'}};
 mount(detail);
 const failure=screen.getByRole('alert',{name:'生成失败详情'});
 expect(failure).toHaveTextContent('模型结果解析');expect(failure).toHaveTextContent('校验输入并限制输出');
 expect(failure).toHaveTextContent('第 1 行，第 2 列');expect(failure).toHaveTextContent('属性名必须使用双引号');
 expect(failure).toHaveTextContent('exec');expect(failure).toHaveTextContent('可以沿用原要求重试');
});
it('switches actual content without generating, trialing or publishing and supports arrow-key focus',async()=>{
 const user=userEvent.setup(),onFeedback=vi.fn();mount(fixture(),{onFeedback});
 const tabs=await screen.findByRole('tablist',{name:'任务内容'});
 expect(within(tabs).getByRole('tab',{name:'改动与发布'})).toHaveAttribute('aria-selected','true');
 expect(screen.queryByRole('heading',{name:'本版优化目标'})).not.toBeInTheDocument();
 await user.click(within(tabs).getByRole('tab',{name:'优化目标'}));
 expect(screen.getByRole('heading',{name:'本版优化目标'})).toBeVisible();
 expect(screen.queryByRole('heading',{name:'本版改动'})).not.toBeInTheDocument();
 await user.keyboard('{End}');expect(within(tabs).getByRole('tab',{name:'发布后效果'})).toHaveFocus();
 expect(screen.getByText('此候选尚未发布，暂无对应部署的观察证据。')).toBeVisible();
 await user.click(screen.getByRole('tab',{name:'改动与发布'}));
 expect(screen.getByRole('heading',{name:'本版改动'})).toBeVisible();
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
 expect(screen.getByText(/自动任务/)).toBeVisible();expect(screen.queryByText(/自动生成草稿 · 等待人工审阅/)).not.toBeInTheDocument();
 expect(screen.getAllByRole('combobox',{name:'查看修复版本'})).toHaveLength(1);
 expect(screen.queryByRole('contentinfo',{name:'当前修复步骤与下一步'})).not.toBeInTheDocument();
 expect(await screen.findByText('暂无新版本运行样本，暂不能判断效果。')).toBeVisible();
});
it('keeps latest recovery separate from a selected historical candidate and shows each version error',async()=>{
 const detail=fixture();const latest={...detail.latestAttempt,revision:2,phase:'failed' as const,draft:null,error:{code:'AIS_FAILED',message:'v2 failure'}};
 detail.latestAttempt=latest;detail.revisions.push(latest);mount(detail);
 expect(screen.getByRole('region',{name:'当前候选'})).toHaveTextContent('当前查看 v1');
 expect(screen.getByText(/最近一次生成 v2/)).toBeVisible();
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
 expect(screen.getByText('已生成修改 1 项 · 未采纳 0 项 · 待确认 0 项')).toBeVisible();
 expect(screen.getByText(/静态检查：通过/)).toBeVisible();expect(screen.getByText(/Mock 检查：未覆盖/)).toBeVisible();
 expect(screen.queryByText('workflow repair candidate: 1/1 items changed')).not.toBeVisible();
 await userEvent.click(await screen.findByRole('button',{name:'重试差异'}));
 await waitFor(()=>expect(api.diff).toHaveBeenCalledTimes(2));
});
it('persists the inspection tab across a close and reopen without opening the scope editor',async()=>{
 const first=mount();await userEvent.click(screen.getByRole('tab',{name:'优化目标'}));first.unmount();mount();
 expect(screen.getByRole('tab',{name:'优化目标'})).toHaveAttribute('aria-selected','true');
 expect(screen.getByRole('heading',{name:'本版优化目标'})).toBeVisible();
});
it('allows read-only navigation but disables publish and source editing',async()=>{
 mount(fixture(),{canEdit:false});expect(await screen.findByRole('button',{name:'发布此版本'})).toBeDisabled();
 await userEvent.click(screen.getByRole('tab',{name:'优化目标'}));
 expect(screen.getByText('当前只有查看权限。')).toBeVisible();expect(screen.getByRole('button',{name:'调整建议与要求'})).toBeDisabled();
 await userEvent.click(screen.getByRole('tab',{name:'发布后效果'}));expect(screen.getByRole('tab',{name:'改动与发布'})).toBeEnabled();
});
it('retains the same publication request after a lost response and inspection tab switches',async()=>{
 api.deploymentTargets.mockResolvedValue({targets:[{id:'workflow:pre:wf',scope:'workflow',label:'工作流 wf',environment:'pre'}]});
 api.publish.mockRejectedValueOnce(new Error('controlled lost response')).mockResolvedValue({status:'running'});
 mount();const user=userEvent.setup();
 await user.click(await screen.findByRole('button',{name:'发布此版本'}));
 await screen.findByText('工作流 wf');await user.click(screen.getByRole('checkbox'));
 await user.click(screen.getByRole('button',{name:'确认发布'}));await screen.findByRole('alert');
 const request=api.publish.mock.calls[0];
 await user.click(screen.getByRole('tab',{name:'优化目标'}));await user.click(screen.getByRole('tab',{name:'发布后效果'}));await user.click(screen.getByRole('tab',{name:'改动与发布'}));
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
 mount(detail);await userEvent.click(screen.getByRole('tab',{name:'优化目标'}));
 expect(screen.getByText('诊断影响：高')).toBeVisible();expect(screen.queryByText(/近期影响次数未知/)).not.toBeInTheDocument();
 await userEvent.click(screen.getByText('原因与依据',{selector:'summary'}));
 expect(screen.getByText('推测：输入缺失')).toBeVisible();expect(screen.getByText('待确认：缺少边界样本')).toBeVisible();
});

it('counts distinct frozen runs from suggestion and diagnosis context alongside direct sources',async()=>{
 const detail=fixture(),item=detail.latestAttempt.input.items[0];
 item.sources=[{kind:'suggestion',suggestionId:'suggestion'}] as never;
 item.context={diagnoses:[{flowId:'run-a',flowIds:['run-a','run-b','',null]}],suggestions:[{runIds:['run-b','run-c',42]},{impactRunIds:['run-d','']},null]};
 detail.latestAttempt.input.items.push({...item,itemId:'direct',sources:[{kind:'diagnosis_candidate',flowId:'run-d'}] as never,context:{}});
 mount(detail);await userEvent.click(screen.getByRole('tab',{name:'优化目标'}));
 expect(screen.getByText(/依据覆盖 4 个运行/)).toBeVisible();expect(screen.queryByText(/来源运行数未知/)).not.toBeInTheDocument();
});

it('shows the selected revision model prompts and optional index while preserving original evidence',async()=>{
 const detail=fixture();
 detail.latestAttempt.draft!.modelInputs=[
  {itemId:'source',coreInput:'Exact core sent for source <script>alert(1)</script>',materials:{root:'/ais/repair-materials/one',files:[{kind:'event',path:'event-one.json',bytes:500,sha256:'abc',purpose:'Read on conflicting evidence',itemIds:['source']}] }},
  {itemId:'source-two',coreInput:'Second call core',materials:{root:'/ais/repair-materials/two',files:[]}},
 ];
 const old={...detail.latestAttempt,revision:0,draft:{candidateCommit:'f'.repeat(40)}};detail.revisions.push(old);
 mount(detail);const user=userEvent.setup();await user.click(screen.getByRole('tab',{name:'优化目标'}));
 expect(screen.getByText('原始建议与证据',{selector:'summary'})).toBeVisible();
 await user.click(screen.getByText('模型输入与材料',{selector:'summary'}));
 const cores=screen.getAllByText('模型核心输入',{selector:'summary'});
 expect(cores).toHaveLength(2);await user.click(cores[0]);await user.click(cores[1]);
 expect(screen.getByText('Exact core sent for source <script>alert(1)</script>')).toBeVisible();
 expect(screen.getByText('Second call core')).toBeVisible();
 expect(document.querySelector('script')).toBeNull();
 const indices=screen.getAllByText('可选材料索引',{selector:'summary'});await user.click(indices[0]);
 expect(screen.getByText(/event-one.json/)).toBeVisible();
 expect(screen.getByText(/Read on conflicting evidence/)).toBeVisible();
 await user.selectOptions(screen.getByRole('combobox',{name:'查看修复版本'}),'0');
 expect(screen.queryByText('Second call core')).not.toBeInTheDocument();
 expect(screen.getByText(/暂无实际模型输入记录/)).toBeInTheDocument();
 expect(api.publish).not.toHaveBeenCalled();
});

it('does not substitute original evidence when an old task has no model input record',async()=>{
 mount();await userEvent.click(screen.getByRole('tab',{name:'优化目标'}));
 expect(screen.getByText(/暂无实际模型输入记录/)).toBeInTheDocument();
 expect(screen.queryByText('模型核心输入',{selector:'summary'})).not.toBeInTheDocument();
 expect(screen.getByText('原始建议与证据',{selector:'summary'})).toBeVisible();
});

it('distinguishes planning and generation calls while preserving legacy input rendering',async()=>{
 const detail=fixture();
 detail.latestAttempt.draft!.modelInputs=[
  {phase:'planning',itemId:'source',coreInput:'Global planning prompt',materials:{root:'/ais/planning',files:[]}},
  {phase:'generation',itemId:'source',coreInput:'Grouped generation prompt',materials:{root:'/ais/generation',files:[]}},
  {itemId:'source-two',coreInput:'Legacy generation prompt',materials:{root:'/ais/legacy',files:[]}},
 ];
 mount(detail);const user=userEvent.setup();await user.click(screen.getByRole('tab',{name:'优化目标'}));
 await user.click(screen.getByText('模型输入与材料',{selector:'summary'}));
 expect(screen.getByText('第 1 次模型调用 · 全局规划')).toBeVisible();
 expect(screen.getByText('第 2 次模型调用 · 生成修改')).toBeVisible();
 expect(screen.getByText('第 3 次模型调用',{exact:true})).toBeVisible();
 for(const core of screen.getAllByText('模型核心输入',{selector:'summary'}))await user.click(core);
 for(const prompt of ['Global planning prompt','Grouped generation prompt','Legacy generation prompt'])expect(screen.getByText(prompt)).toBeVisible();
 expect(screen.getAllByText('可选材料索引',{selector:'summary'})).toHaveLength(3);
 expect(screen.getByText('原始建议与证据',{selector:'summary'})).toBeVisible();
});

it('leads with the task goal and source, keeps version secondary and model materials after goals', async () => {
 const detail=fixture();Object.assign(detail,{title:'修复报告输出格式',source:'automatic'});mount(detail);
 const header=screen.getByRole('region',{name:'当前候选'});
 expect(within(header).getByRole('heading',{name:'修复报告输出格式'})).toBeVisible();
 expect(within(header).getByText('自动任务')).toBeVisible();
 await userEvent.click(screen.getByRole('tab',{name:'优化目标'}));
 const materials=screen.getByText('模型输入与材料',{selector:'summary'}).closest('details')!;
 expect(materials).not.toHaveAttribute('open');
 expect(screen.getByText('原始建议与证据',{selector:'summary'}).compareDocumentPosition(materials)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
 expect(screen.queryByText(/近期影响次数未知/)).not.toBeInTheDocument();
});

it('keeps a completed automatic chain with prior changes reviewable and cancellable after its last no-change batch', async () => {
 const detail=fixture();detail.latestAttempt.phase='no_change';detail.latestAttempt.input.automation={roundId:'daily'} as never;
 detail.automaticProgress={completedBatches:2,totalBatches:2,changedItems:1,publishable:true};
 mount(detail);await userEvent.click(screen.getByText('历史生成记录',{selector:'summary'}));
 expect(screen.getByRole('button',{name:'取消任务'})).toBeEnabled();
 expect(screen.getByRole('button',{name:'发布此版本'})).toBeEnabled();
});
it('shows a compact goal preview for large tasks without losing any item results', async () => {
 const detail=fixture();const first=detail.latestAttempt.input.items[0];
 detail.latestAttempt.input.items=Array.from({length:5},(_,i)=>({...first,itemId:`source-${i}`,proposal:{summary:`目标 ${i}`}}));
 detail.latestAttempt.draft!.itemResults=detail.latestAttempt.input.items.map(i=>({itemId:i.itemId,status:'changed',reason:'已调整'}));
 mount(detail);const panel=within(screen.getByRole('tabpanel',{name:'改动与发布'}));
 expect(panel.getByText('目标 0',{exact:true})).toBeVisible();expect(panel.getByText('目标 4',{exact:true})).not.toBeVisible();
 await userEvent.click(screen.getByText('更多目标处理结果（2 项）',{selector:'summary'}));
 expect(panel.getByText('目标 4',{exact:true})).toBeVisible();
});

it('offers regeneration and cancellation beside publishing for a completed automatic candidate', async () => {
 const detail=fixture();detail.latestAttempt.input.automation={roundId:'daily'} as never;
 detail.automaticProgress={completedBatches:1,totalBatches:1,changedItems:1,publishable:true};
 const onFeedback=vi.fn(),onCancel=vi.fn();mount(detail,{onFeedback,onCancel});
 await userEvent.click(screen.getByRole('button',{name:'调整建议与要求',exact:true}));expect(onFeedback).toHaveBeenCalledOnce();
 await userEvent.click(screen.getByRole('button',{name:'取消任务'}));expect(onCancel).toHaveBeenCalledOnce();
 expect(screen.getByRole('button',{name:'发布此版本'})).toBeEnabled();expect(api.publish).not.toHaveBeenCalled();
});

it('keeps one task action area across tabs without duplicate hidden controls or header shortcuts', async () => {
 const onFeedback=vi.fn(),onCancel=vi.fn();mount(fixture(),{onFeedback,onCancel});
 const user=userEvent.setup();
 for(const tab of ['改动与发布','优化目标','发布后效果']){
  await user.click(screen.getByRole('tab',{name:tab}));
  const actions=screen.getByRole('heading',{name:'任务操作'}).closest('section')!;
  for(const name of ['调整建议与要求','取消任务','发布此版本']){
   expect(screen.getAllByRole('button',{name,exact:true,hidden:true})).toHaveLength(1);
   expect(within(actions).getByRole('button',{name,exact:true})).toBeVisible();
  }
  expect(within(screen.getByRole('region',{name:'当前候选'})).queryByRole('button')).not.toBeInTheDocument();
 }
 expect(screen.queryByRole('button',{name:'查看改动与发布'})).not.toBeInTheDocument();
 await user.click(screen.getByRole('button',{name:'调整建议与要求',exact:true}));expect(onFeedback).toHaveBeenCalledOnce();
 await user.click(screen.getByRole('button',{name:'取消任务'}));expect(onCancel).toHaveBeenCalledOnce();
 expect(api.publish).not.toHaveBeenCalled();
});
it('places failure recovery and the published next-round action in the shared operation area', async () => {
 const detail=fixture();const latest={...detail.latestAttempt,revision:2,phase:'failed' as const,draft:null,error:{code:'AIS_FAILED',message:'failed'}};
 detail.latestAttempt=latest;detail.revisions.push(latest);const onRetryGeneration=vi.fn();const first=mount(detail,{onRetryGeneration});
 const actions=screen.getByRole('heading',{name:'任务操作'}).closest('section')!;
 expect(within(actions).getByText(/重试生成沿用本次建议、修复要求与反馈/)).toBeVisible();
 expect(within(actions).getByRole('button',{name:'重试生成'})).toHaveClass('bg-primary');
 expect(within(actions).getByRole('button',{name:'调整建议与要求'})).not.toHaveClass('bg-primary');
 await userEvent.click(within(actions).getByRole('button',{name:'重试生成'}));expect(onRetryGeneration).toHaveBeenCalledOnce();
 expect(screen.queryByRole('button',{name:'返回上一成功候选审核'})).not.toBeInTheDocument();first.unmount();sessionStorage.clear();
 const published=fixture();published.latestAttempt.phase='published';published.release!.deployments=[{id:'deploy',revision:1,status:'succeeded',baselineDeployNumber:159,receipt:{deployNumber:160}} as never];
 const onNextRound=vi.fn();mount(published,{onNextRound});
 expect(screen.getAllByRole('button',{name:'开始下一轮优化',hidden:true})).toHaveLength(1);
 expect(within(screen.getByRole('region',{name:'当前候选'})).queryByRole('button')).not.toBeInTheDocument();
 await userEvent.click(screen.getByRole('button',{name:'开始下一轮优化'}));expect(onNextRound).toHaveBeenCalledWith(1);
});
