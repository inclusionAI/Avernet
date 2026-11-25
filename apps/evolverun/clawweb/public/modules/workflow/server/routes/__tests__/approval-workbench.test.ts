import { afterEach, expect, it, vi } from 'vitest'
import express from 'express'
import type { Server } from 'node:http'
import { createApprovalWorkbenchRouter } from '../approval-workbench'
import type { ApprovalWorkbenchCard } from '../../services/approval-workbench'
const servers: Server[]=[]
afterEach(async()=>{await Promise.all(servers.splice(0).map(s=>new Promise<void>(r=>s.close(()=>r()))))})
const card: ApprovalWorkbenchCard={id:1,flow_id:'f',node_id:'n',workflow_id:'wf',workflow_title:'流程',message:'审批',approver_ids:'123,456',approved_by:'',rejected_by:'',status:'pending',delivery_mode:'card-web',created_at:100}
async function fixture(){
 const app=express();app.use('/api/approval',createApprovalWorkbenchRouter({scan:async(before)=>before?[]:[card]},async(req)=>{
   if(req.headers.cookie==='session=alice')return {userId:'123',isAdmin:false}
   if(req.headers.cookie==='session=admin')return {userId:'999',isAdmin:true}
   return null
 }))
 const server=app.listen(0,'127.0.0.1');servers.push(server);await new Promise<void>(r=>server.once('listening',r));return `http://127.0.0.1:${(server.address() as any).port}/api/approval/search`
}
it('requires a verified login and rejects spoofed admin headers',async()=>{
 const url=await fixture();expect((await fetch(url)).status).toBe(401)
 expect((await fetch(`${url}?empId=456`,{headers:{cookie:'session=alice','x-user-id':'admin'}})).status).toBe(403)
})
it('uses the session identity by default and allows an admin to query another person',async()=>{
 const url=await fixture();let response=await fetch(url,{headers:{cookie:'session=alice'}});expect(response.status).toBe(200);expect((await response.json()).items).toHaveLength(1)
 response=await fetch(`${url}?empId=456`,{headers:{cookie:'session=admin'}});expect(response.status).toBe(200);expect((await response.json()).items).toHaveLength(1)
})
it('validates filters rather than treating invalid queries as empty results',async()=>{
 const url=await fixture();expect((await fetch(`${url}?personalStatus=bogus`,{headers:{cookie:'session=alice'}})).status).toBe(400)
 expect((await fetch(`${url}?limit=0`,{headers:{cookie:'session=alice'}})).status).toBe(400)
})
it('only exposes workbench details to the assigned user or a verified administrator',async()=>{
 const { createApprovalWorkbenchRouter } = await import('../approval-workbench')
 const app=express();app.use('/api/approval',createApprovalWorkbenchRouter({scan:async()=>[],findRunStatus:async()=> 'waiting',findLatestCardId:async()=>1,findById:async()=>({...card,approval_type:'review',card_fields_json:'[]',approver_names:'Alice,Bob',approval_policy:'any',resolved_at:null,comment:null})},async(req)=>req.headers.cookie==='session=alice'?{userId:'123',isAdmin:false}:req.headers.cookie==='session=admin'?{userId:'999',isAdmin:true}:req.headers.cookie==='session=stranger'?{userId:'888',isAdmin:false}:null))
 const server=app.listen(0,'127.0.0.1');servers.push(server);await new Promise<void>(r=>server.once('listening',r));const base=`http://127.0.0.1:${(server.address() as any).port}/api/approval/workbench/1`
 expect((await fetch(base,{headers:{cookie:'session=stranger'}})).status).toBe(403)
 let result=await fetch(base,{headers:{cookie:'session=alice'}});expect(result.status).toBe(200);expect((await result.json()).canAct).toBe(true)
 result=await fetch(base,{headers:{cookie:'session=admin'}});expect(result.status).toBe(200);expect((await result.json()).canAct).toBe(false)
})
it('supports ISO creation time and validates date and numbered ranges for bot clients',async()=>{
 const url=await fixture()
 expect((await fetch(`${url}?createdFrom=1970-01-01T00%3A00%3A00Z&createdTo=1970-01-01T00%3A02%3A00Z&start=1&end=5`,{headers:{cookie:'session=alice'}})).status).toBe(200)
 expect((await fetch(`${url}?createdFrom=2026-11-03`,{headers:{cookie:'session=alice'}})).status).toBe(400)
 expect((await fetch(`${url}?start=4&end=2`,{headers:{cookie:'session=alice'}})).status).toBe(400)
})
it('returns a configured absolute approval URL and ISO creation time for downstream skill clients',async()=>{
 const { createApprovalWorkbenchRouter }=await import('../approval-workbench')
 const app=express();app.use('/api/approval',createApprovalWorkbenchRouter({scan:async(before)=>before?[]:[card]},async()=>({userId:'123',isAdmin:false}),{publicBaseUrl:'https://clawweb.example'}))
 const server=app.listen(0,'127.0.0.1');servers.push(server);await new Promise<void>(r=>server.once('listening',r))
 const result=await(await fetch(`http://127.0.0.1:${(server.address() as any).port}/api/approval/search`)).json()
 expect(result.items[0]).toMatchObject({workflowId:'wf',workflowTitle:'流程',approverIds:['123','456'],approvalUrl:'https://clawweb.example/approval/1',createdAt:'1970-01-01T00:01:40.000Z'})
})
it('supports the same admin-only cross-person search contract for signed service clients',async()=>{
 const app=express();app.use(express.json());app.use('/api/internal/approval-cards',createApprovalWorkbenchRouter({scan:async(before)=>before?[]:[card]},async req=>req.headers.cookie==='admin'?{userId:'999',isAdmin:true}:req.headers.cookie==='alice'?{userId:'123',isAdmin:false}:null,{allowPost:true}))
 const server=app.listen(0,'127.0.0.1');servers.push(server);await new Promise<void>(r=>server.once('listening',r));const url=`http://127.0.0.1:${(server.address() as any).port}/api/internal/approval-cards/search`
 const call=(cookie:string)=>fetch(url,{method:'POST',headers:{cookie,'Content-Type':'application/json'},body:JSON.stringify({empId:'456',personalStatus:'pending',start:1,end:10})})
 expect((await call('alice')).status).toBe(403)
 const response=await call('admin');expect(response.status).toBe(200);expect((await response.json()).items).toHaveLength(1)
})
it('only lets a verified assigned viewer resolve an active card from the trusted browser origin',async()=>{
 const execute=vi.fn(async(_id:number,actor:string)=>actor === '123' ? ({ok:true,status:'approved' as const,approvedBy:['123'],rejectedBy:[]}) : ({ok:false,status:'pending' as const,approvedBy:[],rejectedBy:[],error:'您不是此审批的授权审批人'}))
 const app=express();app.use(express.json());app.use('/api/approval',createApprovalWorkbenchRouter({scan:async()=>[],resolveActive:execute},async req=>req.headers.cookie==='session=alice'?{userId:'123',isAdmin:false}:req.headers.cookie==='session=admin'?{userId:'999',isAdmin:true}:null,{publicBaseUrl:'https://clawweb.example'}))
 const server=app.listen(0,'127.0.0.1');servers.push(server);await new Promise<void>(r=>server.once('listening',r));const url=`http://127.0.0.1:${(server.address() as any).port}/api/approval/workbench/1/resolve`
 const call=(cookie:string,origin:string)=>fetch(url,{method:'POST',headers:{cookie,origin,'content-type':'application/json'},body:JSON.stringify({action:'approve',empId:'123'})})
 expect((await call('session=alice','https://evil.example')).status).toBe(403)
 expect((await call('','https://clawweb.example')).status).toBe(401)
 expect((await call('session=admin','https://clawweb.example')).status).toBe(403)
 expect((await call('session=alice','https://clawweb.example')).status).toBe(200)
 expect(execute).toHaveBeenCalledWith(1,'123','approve',undefined)
})

it('blocks cross-origin browser reads even when the global CORS policy allows the origin',async()=>{
 const app=express();app.use('/api/approval',createApprovalWorkbenchRouter({scan:async(before)=>before?[]:[card]},async()=>({userId:'123',isAdmin:false}),{publicBaseUrl:'https://clawweb.example'}))
 const server=app.listen(0,'127.0.0.1');servers.push(server);await new Promise<void>(r=>server.once('listening',r));const url=`http://127.0.0.1:${(server.address() as any).port}/api/approval/search`
 expect((await fetch(url,{headers:{origin:'https://other.alipay.com','sec-fetch-site':'same-site'}})).status).toBe(403)
 expect((await fetch(url,{headers:{'sec-fetch-site':'cross-site'}})).status).toBe(403)
 expect((await fetch(url,{headers:{origin:'https://clawweb.example','sec-fetch-site':'same-origin'}})).status).toBe(200)
 expect((await fetch(url)).status).toBe(200)
})
it('restricts all-person search to a verified administrator and validates scope',async()=>{
 const url=await fixture()
 expect((await fetch(`${url}?scope=all`,{headers:{cookie:'session=alice','x-user-id':'admin'}})).status).toBe(403)
 const all=await fetch(`${url}?scope=all&personalStatus=pending`,{headers:{cookie:'session=admin'}});expect(all.status).toBe(200)
 const body=await all.json();expect(body.scope).toBe('all')
 expect(body.items[0].matchedApprovers).toEqual([{empId:'123',personalStatus:'pending'},{empId:'456',personalStatus:'pending'}])
 expect((await fetch(`${url}?scope=everyone`,{headers:{cookie:'session=admin'}})).status).toBe(400)
})
