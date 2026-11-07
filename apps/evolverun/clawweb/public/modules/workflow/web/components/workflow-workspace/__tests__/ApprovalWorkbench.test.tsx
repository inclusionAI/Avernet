import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import ApprovalWorkbench from '../ApprovalWorkbench'

const items = [{cardId:1,flowId:'flow-1',nodeId:'review',workflowId:'expense',workflowTitle:'费用审批',message:'请确认',approverIds:['00123'],status:'pending',personalStatus:'pending',actionability:'available',createdAt:'1970-01-01T00:01:40.000Z',approvalUrl:'/approval/1'}]
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks()})
it('loads current user pending without restricting workflow and can switch to handled',async()=>{
 const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({items,nextCursor:null,scope:'all'})});vi.stubGlobal('fetch',fetcher)
 render(<ApprovalWorkbench isAdmin={false}/>);expect(await screen.findByText('费用审批')).toBeInTheDocument()
 expect(String(fetcher.mock.calls[0][0])).toContain('personalStatus=pending');expect(String(fetcher.mock.calls[0][0])).not.toContain('workflowId=')
 expect(screen.queryByLabelText('审批人工号')).not.toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'已办'}));await waitFor(()=>expect(fetcher.mock.calls.some(c=>String(c[0]).includes('personalStatus=handled'))).toBe(true))
})
it('lets an administrator query another employee explicitly',async()=>{
 const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({items,nextCursor:null,scope:'all'})});vi.stubGlobal('fetch',fetcher)
 render(<ApprovalWorkbench isAdmin/>);await screen.findByText('费用审批')
 fireEvent.click(screen.getByRole('button',{name:'启用管理员'}))
 fireEvent.change(screen.getByLabelText('审批人工号'),{target:{value:'456'}});fireEvent.click(screen.getByRole('button',{name:'查询'}))
 await waitFor(()=>expect(fetcher.mock.calls.some(c=>String(c[0]).includes('empId=456'))).toBe(true))
})
it('shows errors rather than claiming the inbox is empty',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:false,status:403,json:async()=>({message:'无权查询'})}))
 render(<ApprovalWorkbench isAdmin={false}/>);expect(await screen.findByRole('alert')).toHaveTextContent('无权查询')
})
it('copies the row approval link as an absolute URL without page parameters',async()=>{
 const user=userEvent.setup()
 const writeText=vi.spyOn(navigator.clipboard,'writeText').mockResolvedValue(undefined)
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(page(items,null)))
 render(<ApprovalWorkbench isAdmin={false}/>)
 await screen.findByText('费用审批')
 const copyButton=screen.getByRole('button',{name:'复制链接'})
 expect(copyButton).toHaveAttribute('title','复制链接')
 await user.click(copyButton)
 expect(writeText).toHaveBeenCalledWith(new URL('/approval/1',window.location.origin).href)
 expect(await within(copyButton.closest('tr')!).findByRole('status')).toHaveTextContent('已复制')
})

it('copies the server-configured absolute approval URL unchanged',async()=>{
 const user=userEvent.setup()
 const writeText=vi.spyOn(navigator.clipboard,'writeText').mockResolvedValue(undefined)
 const absoluteItems=[{...items[0],approvalUrl:'https://clawweb.example/approval/1'}]
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(page(absoluteItems as typeof items,null)))
 render(<ApprovalWorkbench isAdmin={false}/>)
 await screen.findByText('费用审批')
 await user.click(screen.getByRole('button',{name:'复制链接'}))
 expect(writeText).toHaveBeenCalledWith('https://clawweb.example/approval/1')
})

it('keeps copy feedback scoped to each row',async()=>{
 const user=userEvent.setup()
 vi.spyOn(navigator.clipboard,'writeText').mockResolvedValue(undefined)
 const twoItems=[items[0],{...items[0],cardId:2,approvalUrl:'/approval/2'}]
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(page(twoItems as typeof items,null)))
 render(<ApprovalWorkbench isAdmin={false}/>)
 await screen.findAllByText('费用审批')
 const copyButtons=screen.getAllByRole('button',{name:'复制链接'})
 const rows=copyButtons.map(button => button.closest('tr'))
 await user.click(copyButtons[0])
 await user.click(copyButtons[1])
 const statusByRow=await Promise.all(rows.map(row=>within(row!).findByRole('status')))
 expect(statusByRow[0]).toHaveTextContent('已复制')
 expect(statusByRow[1]).toHaveTextContent('已复制')
})

it('reports clipboard failure without claiming that the link was copied',async()=>{
 const user=userEvent.setup()
 vi.spyOn(navigator.clipboard,'writeText').mockRejectedValue(new Error('denied'))
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(page(items,null)))
 render(<ApprovalWorkbench isAdmin={false}/>)
 await screen.findByText('费用审批')
 const copyButton=screen.getByRole('button',{name:'复制链接'})
 await user.click(copyButton)
 expect(await within(copyButton.closest('tr')!).findByRole('status')).toHaveTextContent('复制失败')
 expect(screen.queryByText('已复制')).not.toBeInTheDocument()
})

it('approves an assigned task in the workbench and refreshes the list',async()=>{
 const detail={id:1,flowId:'flow-1',nodeId:'review',workflowId:'expense',workflowTitle:'费用审批',approvalType:null,message:'请确认',cardFields:[],approverIds:['00123'],approverNames:['审批人'],approvalPolicy:'any',approvedBy:[],rejectedBy:[],status:'pending',deliveryMode:'card-web',createdAt:100,resolvedAt:null,canAct:true}
 const fetcher=vi.fn(async (url:string,options?:RequestInit)=>{
  if(url.includes('/api/approval/1/resolve/session')) return {ok:true,json:async()=>({ok:true,status:'approved'})}
  if(url.includes('/workbench/')) return {ok:true,json:async()=>detail}
  return {ok:true,json:async()=>({items,nextCursor:null})}
 });vi.stubGlobal('fetch',fetcher)
 render(<ApprovalWorkbench isAdmin={false}/>);fireEvent.click(await screen.findByRole('button',{name:'进行审批'}))
 const button=await screen.findByRole('button',{name:'通过'});fireEvent.click(button)
 await waitFor(()=>expect(fetcher.mock.calls.some(([url,options])=>String(url).includes('/api/approval/1/resolve/session') && options?.method==='POST')).toBe(true))
 await waitFor(()=>expect(fetcher.mock.calls.filter(([url])=>String(url).includes('/search?')).length).toBeGreaterThan(1))
})
it('does not offer approval actions when the viewer is not an approver',async()=>{
 const detail={id:1,flowId:'flow-1',nodeId:'review',workflowId:'expense',workflowTitle:'费用审批',approvalType:null,message:'请确认',cardFields:[],approverIds:['00123'],approverNames:['审批人'],approvalPolicy:'any',approvedBy:[],rejectedBy:[],status:'pending',deliveryMode:'card-web',createdAt:100,resolvedAt:null,canAct:false}
 vi.stubGlobal('fetch',vi.fn(async (url:string)=>({ok:true,json:async()=>url.includes('/workbench/')?detail: url.includes('/search?')?{items,nextCursor:null,scope:'all'}:{items,nextCursor:null}})))
 render(<ApprovalWorkbench isAdmin/>);fireEvent.click(await screen.findByRole('button',{name:'启用管理员'}));fireEvent.click(await screen.findByRole('button',{name:'查看详情'}))
 await screen.findByText('请确认',{selector:'h3'});expect(screen.queryByRole('button',{name:'通过'})).not.toBeInTheDocument()
})


type SearchResponse = {ok:boolean; json:()=>Promise<{items:typeof items; nextCursor:string|null; message?:string; scope?:string}>}
function deferred<T>() {
 let resolve!:(value:T)=>void, reject!:(error:Error)=>void
 const promise=new Promise<T>((res,rej)=>{resolve=res;reject=rej})
 return {promise,resolve,reject}
}
function page(pageItems:typeof items,nextCursor:string|null):SearchResponse {
 return {ok:true,json:async()=>({items:pageItems,nextCursor,scope:'all'})}
}
const oldPageItem={...items[0],cardId:2,workflowTitle:'旧查询的第二页'}
const newPageItem={...items[0],cardId:3,workflowTitle:'新查询的结果'}

it.each(['tab','filter'] as const)('ignores a delayed page after changing %s, including its cursor',async change=>{
 const oldPage=deferred<SearchResponse>()
 const fetcher=vi.fn().mockResolvedValueOnce(page(items,'old-cursor')).mockReturnValueOnce(oldPage.promise)
  .mockResolvedValueOnce(page([newPageItem],'new-cursor')).mockResolvedValueOnce(page([],null))
 vi.stubGlobal('fetch',fetcher)
 render(<ApprovalWorkbench isAdmin/>)
 await screen.findByText('费用审批')
 fireEvent.click(screen.getByRole('button',{name:'下一页'}))
 if(change==='tab') fireEvent.click(screen.getByRole('button',{name:'已办'}))
 else {
  fireEvent.change(screen.getByLabelText('工作流 ID'),{target:{value:'other'}})
  fireEvent.click(screen.getByRole('button',{name:'查询'}))
 }
 await screen.findByText('新查询的结果')
 await act(async()=>{oldPage.resolve(page([oldPageItem],'stale-cursor'));await oldPage.promise})
 expect(screen.queryByText('旧查询的第二页')).not.toBeInTheDocument()
 expect(screen.getByText('新查询的结果')).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'下一页'}))
 await waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(4))
 expect(String(fetcher.mock.calls[3][0])).toContain('cursor=new-cursor')
})

it('ignores a delayed page error and keeps the new query loading until it completes',async()=>{
 const oldPage=deferred<SearchResponse>(),newPage=deferred<SearchResponse>()
 vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(page(items,'old-cursor')).mockReturnValueOnce(oldPage.promise).mockReturnValueOnce(newPage.promise))
 render(<ApprovalWorkbench isAdmin={false}/>)
 await screen.findByText('费用审批')
 fireEvent.click(screen.getByRole('button',{name:'下一页'}))
 fireEvent.click(screen.getByRole('button',{name:'已办'}))
 await act(async()=>{oldPage.reject(new Error('旧请求失败'));await oldPage.promise.catch(()=>{})})
 expect(screen.queryByRole('alert')).not.toBeInTheDocument()
 expect(screen.getByRole('status')).toHaveTextContent('加载中')
 await act(async()=>{newPage.resolve(page([newPageItem],null));await newPage.promise})
 expect(await screen.findByText('新查询的结果')).toBeInTheDocument()
 expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

it('replaces the page and advances the cursor when the query has not changed',async()=>{
 const nextPage=deferred<SearchResponse>()
 const fetcher=vi.fn().mockResolvedValueOnce(page(items,'first-cursor')).mockReturnValueOnce(nextPage.promise).mockResolvedValueOnce(page([],null))
 vi.stubGlobal('fetch',fetcher)
 render(<ApprovalWorkbench isAdmin={false}/>)
 await screen.findByText('费用审批')
 fireEvent.click(screen.getByRole('button',{name:'下一页'}))
 await act(async()=>{nextPage.resolve(page([oldPageItem],'second-cursor'));await nextPage.promise})
 expect(screen.queryByText('费用审批')).not.toBeInTheDocument()
 expect(screen.getByText('旧查询的第二页')).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'下一页'}))
 await waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(3))
 expect(String(fetcher.mock.calls[2][0])).toContain('cursor=second-cursor')
})

it('ignores an old page after resetting the same query',async()=>{
 const oldPage=deferred<SearchResponse>()
 const fetcher=vi.fn().mockResolvedValueOnce(page(items,'old-cursor')).mockReturnValueOnce(oldPage.promise)
  .mockResolvedValueOnce(page([newPageItem],null))
 vi.stubGlobal('fetch',fetcher)
 render(<ApprovalWorkbench isAdmin={false}/>)
 await screen.findByText('费用审批')
 fireEvent.click(screen.getByRole('button',{name:'下一页'}))
 fireEvent.click(screen.getByRole('button',{name:'重置'}))
 await screen.findByText('新查询的结果')
 await act(async()=>{oldPage.resolve(page([oldPageItem],'stale-cursor'));await oldPage.promise})
 expect(screen.queryByText('旧查询的第二页')).not.toBeInTheDocument()
 expect(screen.getByRole('button',{name:'下一页'})).toBeDisabled()
})

it('ignores an old initial response even if fetch resolves after being aborted',async()=>{
 const oldInitial=deferred<SearchResponse>()
 vi.stubGlobal('fetch',vi.fn().mockReturnValueOnce(oldInitial.promise).mockResolvedValueOnce(page([newPageItem],null)))
 render(<ApprovalWorkbench isAdmin={false}/>)
 fireEvent.click(screen.getByRole('button',{name:'已办'}))
 await screen.findByText('新查询的结果')
 await act(async()=>{oldInitial.resolve(page(items,'stale-cursor'));await oldInitial.promise})
 expect(screen.getByText('新查询的结果')).toBeInTheDocument()
 expect(screen.queryByText('费用审批')).not.toBeInTheDocument()
 expect(screen.getByRole('button',{name:'下一页'})).toBeDisabled()
})
it('requires admins to enable admin mode before querying everyone or another employee',async()=>{
 const fetcher=vi.fn().mockResolvedValue(page(items,null));vi.stubGlobal('fetch',fetcher)
 render(<ApprovalWorkbench isAdmin userId="100"/>);await screen.findByText('费用审批')
 expect(screen.getByLabelText('审批人工号')).toBeDisabled()
 expect(String(fetcher.mock.calls[0][0])).not.toContain('scope=all')
 fireEvent.click(screen.getByRole('button',{name:'启用管理员'}))
 await waitFor(()=>expect(fetcher.mock.calls.some(c=>String(c[0]).includes('scope=all'))).toBe(true))
 expect(screen.getByLabelText('审批人工号')).not.toBeDisabled()
 fireEvent.change(screen.getByLabelText('审批人工号'),{target:{value:'456'}});fireEvent.click(screen.getByRole('button',{name:'查询'}))
 await waitFor(()=>expect(fetcher.mock.calls.some(c=>String(c[0]).includes('empId=456')&&!String(c[0]).includes('scope=all'))).toBe(true))
 fireEvent.click(screen.getByRole('button',{name:'关闭管理员'}))
 await waitFor(()=>expect(screen.getByLabelText('审批人工号')).toBeDisabled())
})
it('shows previous and next pages without appending results and supports page size',async()=>{
 const fetcher=vi.fn().mockResolvedValueOnce(page(items,'page2')).mockResolvedValueOnce(page([oldPageItem],null)).mockResolvedValueOnce(page(items,'page2')).mockResolvedValueOnce(page(items,null));vi.stubGlobal('fetch',fetcher)
 render(<ApprovalWorkbench isAdmin={false}/>);await screen.findByText('费用审批')
 fireEvent.click(screen.getByRole('button',{name:'下一页'}));await screen.findByText('旧查询的第二页')
 expect(screen.queryByText('费用审批')).not.toBeInTheDocument();expect(screen.getByText('第 2 页')).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'上一页'}));await screen.findByText('费用审批')
 fireEvent.change(screen.getByLabelText('每页条数'),{target:{value:'50'}})
 await waitFor(()=>expect(fetcher.mock.calls.some(c=>String(c[0]).includes('limit=50'))).toBe(true))
})
it('does not show admin controls to ordinary users',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(page(items,null)))
 render(<ApprovalWorkbench isAdmin={false} userId="100"/>);await screen.findByText('费用审批')
 expect(screen.queryByRole('button',{name:'启用管理员'})).not.toBeInTheDocument()
})
it('shows each matching approver status on a shared card in administrator mode',async()=>{
 const mixed={...items[0],matchedApprovers:[{empId:'00123',personalStatus:'approved'},{empId:'456',personalStatus:'rejected'}]}
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(page([mixed] as typeof items,null)))
 render(<ApprovalWorkbench isAdmin/>);await screen.findByText('费用审批')
 fireEvent.click(screen.getByRole('button',{name:'启用管理员'}));await screen.findByText('00123：已同意')
 expect(screen.getByText('456：已驳回')).toBeInTheDocument()
})
it('exits administrator mode when admin privileges disappear',async()=>{
 const fetcher=vi.fn().mockResolvedValue(page(items,null));vi.stubGlobal('fetch',fetcher)
 const {rerender}=render(<ApprovalWorkbench isAdmin userId="100"/>);await screen.findByText('费用审批')
 fireEvent.click(screen.getByRole('button',{name:'启用管理员'}));await waitFor(()=>expect(fetcher.mock.calls.some(c=>String(c[0]).includes('scope=all'))).toBe(true))
 rerender(<ApprovalWorkbench isAdmin={false} userId="100"/>);await waitFor(()=>expect(String(fetcher.mock.calls.at(-1)?.[0])).not.toContain('scope=all'))
 expect(screen.queryByRole('button',{name:'关闭管理员'})).not.toBeInTheDocument()
})
it('does not claim an old backend queried everyone when it ignores the all-person scope',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>({items:[],nextCursor:null})}))
 render(<ApprovalWorkbench isAdmin/>);await screen.findByText('当前没有符合条件的审批')
 fireEvent.click(screen.getByRole('button',{name:'启用管理员'}))
 expect(await screen.findByRole('alert')).toHaveTextContent('尚不支持管理员全部人员查询')
 expect(screen.queryByText('当前没有符合条件的审批')).not.toBeInTheDocument()
})
