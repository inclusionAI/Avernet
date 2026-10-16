import { render, screen } from '@testing-library/react'
import { QueryClient,QueryClientProvider } from '@tanstack/react-query'
import { beforeEach,expect,it,vi } from 'vitest'
import userEvent from '@testing-library/user-event'
const api=vi.hoisted(()=>({verification:vi.fn()}));vi.mock('../../../../api/repair-batches',()=>({repairBatches:api}))
import RepairVerification from '../RepairVerification'
const deployment={id:'deployment-ui',revision:1,status:'succeeded',baselineDeployNumber:159,receipt:{deployNumber:160}} as never
const detail={taskId:'verify-ui',workflowId:'wf',revisions:[{revision:1,input:{baseline:{activeDeployNumber:159},items:[{itemId:'item',proposal:{summary:'修复超时'}}]}}]} as never
function mount(){render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><RepairVerification detail={detail} deployment={deployment} canEdit={false}/></QueryClientProvider>)}
const evidence=(automatic:unknown[]=[],total=0)=>({evidence:{deployNumber:160,total,excludedUnknownVersions:0,samples:[],automatic},conclusions:[]})
beforeEach(()=>vi.clearAllMocks())
it('distinguishes no samples from having no verifier',async()=>{
 api.verification.mockResolvedValue(evidence());mount();
 expect(await screen.findByText('暂无新版本运行样本，暂不能判断效果。')).toBeVisible();
 expect(screen.getByRole('link',{name:'查看版本对比'})).toHaveAttribute('href','/workflows/workspace?workflowId=wf&tab=overview&view=version-comparison&baseline=159&current=160');
});
it('shows a missing verification rule without claiming a successful repair',async()=>{
 api.verification.mockResolvedValue(evidence([{itemId:'item',result:'observing',rule:null,eligibleSamples:0,reason:'no rule'}],3));mount();
 expect(await screen.findByText('暂无适用验证规则，暂不能判断效果。')).toBeVisible();expect(screen.queryByText('符合验证规则')).not.toBeInTheDocument();
});
it('front-loads exact rule sample progress and folds technical identifiers',async()=>{
 api.verification.mockResolvedValue(evidence([{itemId:'item',result:'observing',rule:{version:'node-timeout/v1',nodeId:'query',signature:'private-rule-signature',windowDays:7,minimumSamples:5},eligibleSamples:2,reason:'continue observing',sampleSummary:{related:3,complete:2,missingAnalysis:1,unsuccessfulNode:0}}],3));mount();
 expect(await screen.findByText('有效样本 2/5 · 观察窗口 7 天')).toBeVisible();
 expect(screen.getByText(/缺少完整分析 1 次/)).toBeVisible();expect(screen.getByText(/private-rule-signature/)).not.toBeVisible();
});
it('surfaces a read failure with a read-only retry',async()=>{
 api.verification.mockRejectedValueOnce(new Error('controlled read failure')).mockResolvedValue(evidence());mount();
 expect(await screen.findByText('观察数据读取失败，暂不能判断效果。')).toBeVisible();
 await userEvent.click(screen.getByRole('button',{name:'重试读取证据'}));expect(await screen.findByText('暂无新版本运行样本，暂不能判断效果。')).toBeVisible();expect(api.verification).toHaveBeenCalledTimes(2);
});
it('reports recurrence separately from a sample success rate',async()=>{
 api.verification.mockResolvedValue(evidence([{itemId:'item',result:'ineffective',rule:null,eligibleSamples:0,reason:'same issue recurred'}],1));mount();
 expect(await screen.findByText('原问题复现，需继续优化。')).toBeVisible();expect(screen.queryByText(/成功率提升/)).not.toBeInTheDocument();
});
