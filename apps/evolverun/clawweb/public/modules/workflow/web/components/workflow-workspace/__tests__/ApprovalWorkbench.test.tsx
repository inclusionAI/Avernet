import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import ApprovalWorkbench from '../ApprovalWorkbench'

const items = [{cardId:1,flowId:'flow-1',nodeId:'review',workflowId:'expense',workflowTitle:'费用审批',message:'请确认',approverIds:['00123'],status:'pending',personalStatus:'pending',createdAt:'1970-01-01T00:01:40.000Z',approvalUrl:'/approval/1'}]
afterEach(()=>vi.unstubAllGlobals())
it('loads current user pending without restricting workflow and can switch to handled',async()=>{
 const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({items,nextCursor:null})});vi.stubGlobal('fetch',fetcher)
 render(<ApprovalWorkbench isAdmin={false}/>);expect(await screen.findByText('费用审批')).toBeInTheDocument()
 expect(String(fetcher.mock.calls[0][0])).toContain('personalStatus=pending');expect(String(fetcher.mock.calls[0][0])).not.toContain('workflowId=')
 expect(screen.queryByLabelText('审批人工号')).not.toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'已办'}));await waitFor(()=>expect(fetcher.mock.calls.some(c=>String(c[0]).includes('personalStatus=handled'))).toBe(true))
})
it('lets an administrator query another employee explicitly',async()=>{
 const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({items,nextCursor:null})});vi.stubGlobal('fetch',fetcher)
 render(<ApprovalWorkbench isAdmin/>);await screen.findByText('费用审批')
 fireEvent.change(screen.getByLabelText('审批人工号'),{target:{value:'456'}});fireEvent.click(screen.getByRole('button',{name:'查询'}))
 await waitFor(()=>expect(fetcher.mock.calls.some(c=>String(c[0]).includes('empId=456'))).toBe(true))
})
it('shows errors rather than claiming the inbox is empty',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:false,status:403,json:async()=>({message:'无权查询'})}))
 render(<ApprovalWorkbench isAdmin={false}/>);expect(await screen.findByRole('alert')).toHaveTextContent('无权查询')
})
it('approves an assigned task in the workbench and refreshes the list',async()=>{
 const detail={id:1,flowId:'flow-1',nodeId:'review',workflowId:'expense',workflowTitle:'费用审批',approvalType:null,message:'请确认',cardFields:[],approverIds:['00123'],approverNames:['审批人'],approvalPolicy:'any',approvedBy:[],rejectedBy:[],status:'pending',deliveryMode:'card-web',createdAt:100,resolvedAt:null,canAct:true}
 const fetcher=vi.fn(async (url:string,options?:RequestInit)=>{
  if(url.includes('/workbench/1/resolve')) return {ok:true,json:async()=>({ok:true,status:'approved'})}
  if(url.includes('/workbench/')) return {ok:true,json:async()=>detail}
  return {ok:true,json:async()=>({items,nextCursor:null})}
 });vi.stubGlobal('fetch',fetcher)
 render(<ApprovalWorkbench isAdmin={false}/>);fireEvent.click(await screen.findByRole('button',{name:'查看详情'}))
 const button=await screen.findByRole('button',{name:'通过'});fireEvent.click(button)
 await waitFor(()=>expect(fetcher.mock.calls.some(([url,options])=>String(url).includes('/workbench/1/resolve') && options?.method==='POST')).toBe(true))
 await waitFor(()=>expect(fetcher.mock.calls.filter(([url])=>String(url).includes('/search?')).length).toBeGreaterThan(1))
})
it('does not offer approval actions when the viewer is not an approver',async()=>{
 const detail={id:1,flowId:'flow-1',nodeId:'review',workflowId:'expense',workflowTitle:'费用审批',approvalType:null,message:'请确认',cardFields:[],approverIds:['00123'],approverNames:['审批人'],approvalPolicy:'any',approvedBy:[],rejectedBy:[],status:'pending',deliveryMode:'card-web',createdAt:100,resolvedAt:null,canAct:false}
 vi.stubGlobal('fetch',vi.fn(async (url:string)=>({ok:true,json:async()=>url.includes('/workbench/')?detail:{items,nextCursor:null}})))
 render(<ApprovalWorkbench isAdmin/>);fireEvent.click(await screen.findByRole('button',{name:'查看详情'}))
 await screen.findByText('请确认',{selector:'h3'});expect(screen.queryByRole('button',{name:'通过'})).not.toBeInTheDocument()
})


type SearchResponse = {ok:boolean; json:()=>Promise<{items:typeof items; nextCursor:string|null; message?:string}>}
function deferred<T>() {
 let resolve!:(value:T)=>void, reject!:(error:Error)=>void
 const promise=new Promise<T>((res,rej)=>{resolve=res;reject=rej})
 return {promise,resolve,reject}
}
function page(pageItems:typeof items,nextCursor:string|null):SearchResponse {
 return {ok:true,json:async()=>({items:pageItems,nextCursor})}
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
 fireEvent.click(screen.getByRole('button',{name:'加载更多'}))
 if(change==='tab') fireEvent.click(screen.getByRole('button',{name:'已办'}))
 else {
  fireEvent.change(screen.getByLabelText('审批人工号'),{target:{value:'456'}})
  fireEvent.click(screen.getByRole('button',{name:'查询'}))
 }
 await screen.findByText('新查询的结果')
 await act(async()=>{oldPage.resolve(page([oldPageItem],'stale-cursor'));await oldPage.promise})
 expect(screen.queryByText('旧查询的第二页')).not.toBeInTheDocument()
 expect(screen.getByText('新查询的结果')).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'加载更多'}))
 await waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(4))
 expect(String(fetcher.mock.calls[3][0])).toContain('cursor=new-cursor')
})

it('ignores a delayed page error and keeps the new query loading until it completes',async()=>{
 const oldPage=deferred<SearchResponse>(),newPage=deferred<SearchResponse>()
 vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(page(items,'old-cursor')).mockReturnValueOnce(oldPage.promise).mockReturnValueOnce(newPage.promise))
 render(<ApprovalWorkbench isAdmin={false}/>)
 await screen.findByText('费用审批')
 fireEvent.click(screen.getByRole('button',{name:'加载更多'}))
 fireEvent.click(screen.getByRole('button',{name:'已办'}))
 await act(async()=>{oldPage.reject(new Error('旧请求失败'));await oldPage.promise.catch(()=>{})})
 expect(screen.queryByRole('alert')).not.toBeInTheDocument()
 expect(screen.getByRole('status')).toHaveTextContent('加载中')
 await act(async()=>{newPage.resolve(page([newPageItem],null));await newPage.promise})
 expect(await screen.findByText('新查询的结果')).toBeInTheDocument()
 expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

it('appends a page and advances the cursor when the query has not changed',async()=>{
 const nextPage=deferred<SearchResponse>()
 const fetcher=vi.fn().mockResolvedValueOnce(page(items,'first-cursor')).mockReturnValueOnce(nextPage.promise).mockResolvedValueOnce(page([],null))
 vi.stubGlobal('fetch',fetcher)
 render(<ApprovalWorkbench isAdmin={false}/>)
 await screen.findByText('费用审批')
 fireEvent.click(screen.getByRole('button',{name:'加载更多'}))
 await act(async()=>{nextPage.resolve(page([oldPageItem],'second-cursor'));await nextPage.promise})
 expect(screen.getByText('费用审批')).toBeInTheDocument()
 expect(screen.getByText('旧查询的第二页')).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'加载更多'}))
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
 fireEvent.click(screen.getByRole('button',{name:'加载更多'}))
 fireEvent.click(screen.getByRole('button',{name:'重置'}))
 await screen.findByText('新查询的结果')
 await act(async()=>{oldPage.resolve(page([oldPageItem],'stale-cursor'));await oldPage.promise})
 expect(screen.queryByText('旧查询的第二页')).not.toBeInTheDocument()
 expect(screen.queryByRole('button',{name:'加载更多'})).not.toBeInTheDocument()
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
 expect(screen.queryByRole('button',{name:'加载更多'})).not.toBeInTheDocument()
})
