use crate::{ApiState, mapping, sse};
use a2a::{A2AError, JsonRpcRequest, JsonRpcResponse, JsonRpcId};
use axum::{extract::{State, Request}, body::to_bytes, response::{IntoResponse, Response}, Json, http::StatusCode};
use bcn_runtime_model::*;
use futures::StreamExt;
use serde::de::DeserializeOwned;
use serde_json::Value;
fn params<T: DeserializeOwned>(value: Option<Value>) -> Result<T,A2AError> {
    serde_json::from_value(value.unwrap_or_else(||serde_json::json!({}))).map_err(|_|A2AError::invalid_params("invalid parameters"))
}
pub fn failure(id: JsonRpcId, e: A2AError) -> Response { Json(JsonRpcResponse::error(id,e.to_jsonrpc_error())).into_response() }
fn success<T: serde::Serialize>(id:JsonRpcId,value:T)->Response { Json(JsonRpcResponse::success(id,serde_json::to_value(value).expect("protocol serialization"))).into_response() }
pub async fn handle(State(state):State<ApiState>, req:Request)->Response {
    let (parts,body)=req.into_parts();
    let bytes=match to_bytes(body,crate::MAX_BODY_BYTES).await { Ok(v)=>v, Err(_)=>return (StatusCode::PAYLOAD_TOO_LARGE,failure(JsonRpcId::Null,A2AError::invalid_request("request body exceeds limit"))).into_response() };
    let rpc:JsonRpcRequest=match serde_json::from_slice(&bytes) { Ok(v)=>v,Err(_)=>return failure(JsonRpcId::Null,A2AError::parse_error("invalid JSON-RPC request")) };
    let id=rpc.id.clone();
    if rpc.jsonrpc!="2.0" { return failure(id,A2AError::invalid_request("JSON-RPC 2.0 required")); }
    let version=parts.headers.get("a2a-version").and_then(|v|v.to_str().ok()).unwrap_or("");
    let versions:Vec<_>=version.split('.').collect();
    if !(versions.len()==2 || versions.len()==3) || versions[0]!="1" || versions[1]!="0" || versions.get(2).is_some_and(|v|v.is_empty() || !v.bytes().all(|c|c.is_ascii_digit())) {
        return failure(id,A2AError::version_not_supported(if version.is_empty(){"0.3"}else{version}));
    }
    let ctx=match state.auth.authenticate(&parts.headers).await {Ok(v)=>v,Err(e)=>return (StatusCode::UNAUTHORIZED,failure(id,mapping::error(e))).into_response()};
    let result=dispatch(state,ctx,rpc,&parts.headers).await;
    match result {Ok(response)=>response,Err(e)=>failure(id,e)}
}
async fn dispatch(state:ApiState,ctx:CallerContext,rpc:JsonRpcRequest,headers:&axum::http::HeaderMap)->Result<Response,A2AError>{
    let target_scope=match &state.target {ExecutionTarget::Agent{agent_id}=>&agent_id.scope,ExecutionTarget::Group{group_id}=>&group_id.scope};
    if ctx.scope!=*target_scope || ctx.principal.trim().is_empty(){return Err(mapping::error(RuntimeError::AccessDenied))}
    if rpc.params.as_ref().and_then(|v|v.get("tenant")).is_some_and(|t|!t.is_null() && t.as_str()!=Some(&ctx.scope.tenant)){return Err(mapping::error(RuntimeError::AccessDenied))}
    let id=rpc.id;
    match rpc.method.as_str(){
        "SendMessage"|"SendStreamingMessage"=>{
            let request:a2a::SendMessageRequest=params(rpc.params)?;
            let immediate=request.configuration.as_ref().and_then(|v|v.return_immediately).unwrap_or(false);
            let request=mapping::submit(&ctx,state.target.clone(),request)?;
            let outcome=state.gateway.submit_with_receipt(ctx.clone(),request).await.map_err(mapping::error)?;
            let reply=outcome.reply_interaction_id;
            let mut view=outcome.task;
            if rpc.method=="SendStreamingMessage" {
                let stream=state.gateway.subscribe(ctx.clone(),view.task_id.clone(),None).await;
                return match stream {Ok(stream)=>Ok(sse::response(state,ctx,id,view,stream)),Err(RuntimeError::Conflict(_))=>{
                    view=state.gateway.get(ctx,view.task_id).await.map_err(mapping::error)?;
                    if view.status.is_terminal(){Ok(sse::terminal(id,view))}else{Err(A2AError::internal("subscription conflict"))}
                },Err(e)=>Err(mapping::error(e))};
            }
            if !immediate && !reply_ready(&view,reply.as_ref()) {
                match state.gateway.subscribe(ctx.clone(),view.task_id.clone(),None).await {
                    Ok(mut events)=>while let Some(event)=events.next().await {
                        let event=event.map_err(mapping::error)?;
                        let next=match event.kind {TaskEventKind::Reset{snapshot}=>snapshot,_=>state.gateway.get(ctx.clone(),view.task_id.clone()).await.map_err(mapping::error)?};
                        if reply_ready(&next,reply.as_ref()) {view=next;break;} view=next;
                    },
                    Err(RuntimeError::Conflict(_))=>view=state.gateway.get(ctx.clone(),view.task_id.clone()).await.map_err(mapping::error)?,
                    Err(e)=>return Err(mapping::error(e)),
                }
            }
            Ok(success(id,a2a::SendMessageResponse::Task(mapping::task(&view))))
        }
        "GetTask"=>{let r:a2a::GetTaskRequest=params(rpc.params)?;mapping::history(r.history_length)?;Ok(success(id,mapping::task(&get(&state,&ctx,&r.id).await?)))}
        "ListTasks"=>{
            let r:a2a::ListTasksRequest=params(rpc.params)?;mapping::history(r.history_length)?;
            if r.status.is_some()||r.status_timestamp_after.is_some(){return Err(A2AError::unsupported_operation("status filters are unavailable"))}
            let limit=r.page_size.unwrap_or(50);if !(1..=100).contains(&limit){return Err(A2AError::invalid_params("pageSize must be 1..100"))}
            if let Some(c)=&r.context_id{mapping::id(c)?;}
            let page=state.gateway.list(ctx.clone(),TaskQuery{target:Some(state.target.clone()),session_id:r.context_id.map(|v|SessionId::new(ctx.scope.clone(),v)),cursor:r.page_token.filter(|s|!s.is_empty()),limit:limit as u32}).await.map_err(mapping::error)?;
            let mut tasks:Vec<_>=page.items.iter().map(mapping::task).collect();if !r.include_artifacts.unwrap_or(false){for t in &mut tasks{t.artifacts=None;}}else{for t in &mut tasks{if t.artifacts.is_none(){t.artifacts=Some(vec![]);}}}
            Ok(success(id,a2a::ListTasksResponse{total_size:i32::try_from(page.total_size).map_err(|_|A2AError::internal("task count exceeds protocol range"))?,tasks,next_page_token:page.next_cursor.unwrap_or_default(),page_size:limit}))
        }
        "CancelTask"=>{
            let r:a2a::CancelTaskRequest=params(rpc.params)?;let task=get(&state,&ctx,&r.id).await?;
            if task.status.is_terminal(){return Err(A2AError::task_not_cancelable(&r.id))}
            let operation=OperationId::new(ctx.scope.clone(),format!("cancel:{}",r.id));
            let view=state.gateway.cancel(ctx,CancelCommand{task_id:task.task_id,operation_id:operation}).await.map_err(mapping::error)?;Ok(success(id,mapping::task(&view)))
        }
        "SubscribeToTask"=>{
            let r:a2a::SubscribeToTaskRequest=params(rpc.params)?;let task=get(&state,&ctx,&r.id).await?;
            if task.status.is_terminal(){return Err(A2AError::unsupported_operation("terminal task must be queried"))}
            let negotiated=headers.get_all("a2a-extensions").iter().filter_map(|v|v.to_str().ok())
                .flat_map(|v|v.split(',')).any(|v|v.trim()==mapping::STREAM);
            let cursor=if negotiated {headers.get("last-event-id").map(|v|v.to_str().map_err(|_|A2AError::invalid_params("invalid cursor")).and_then(sse::cursor)).transpose()?}else{None};
            let stream=state.gateway.subscribe(ctx.clone(),task.task_id.clone(),cursor).await.map_err(mapping::error)?;
            let mut response=sse::response(state,ctx,id,task,stream);
            if negotiated {response.headers_mut().insert("a2a-extensions",axum::http::HeaderValue::from_static(mapping::STREAM));}
            Ok(response)
        }
        "CreateTaskPushNotificationConfig"|"GetTaskPushNotificationConfig"|"ListTaskPushNotificationConfigs"|"DeleteTaskPushNotificationConfig"=>Err(A2AError::push_notification_not_supported()),
        "GetExtendedAgentCard"=>Err(A2AError::unsupported_operation("extended card unavailable")),
        _=>Err(A2AError::method_not_found(&rpc.method)),
    }
}
async fn get(state:&ApiState,ctx:&CallerContext,id:&str)->Result<TaskView,A2AError>{
    mapping::id(id)?;let task=state.gateway.get(ctx.clone(),TaskId::new(ctx.scope.clone(),id)).await.map_err(mapping::error)?;
    if task.target!=state.target{return Err(mapping::error(RuntimeError::AccessDenied))}Ok(task)
}
fn interrupted(t:&TaskView)->bool{t.status.is_terminal()||!t.active_interactions.is_empty()||t.status==TaskStatus::AuthRequired}

fn reply_ready(t:&TaskView,reply:Option<&InteractionId>)->bool{
    interrupted(t) && (t.status.is_terminal() || reply.is_none_or(|id|!t.active_interactions.iter().any(|i|&i.interaction_id==id)))
}
