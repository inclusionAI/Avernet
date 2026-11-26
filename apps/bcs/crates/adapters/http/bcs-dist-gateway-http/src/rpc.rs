//! The A2A JSON-RPC endpoint.

use crate::error::a2a_error;
use crate::mapping::{self, TaskShape};
use crate::{AppState, cursor, stream};
use a2a::{
    A2AError, CancelTaskRequest, GetTaskRequest, JsonRpcId, JsonRpcRequest, JsonRpcResponse,
    ListTasksRequest, ListTasksResponse, Message, SendMessageRequest, SendMessageResponse,
    SubscribeToTaskRequest, methods,
};
use axum::Json;
use axum::extract::rejection::JsonRejection;
use axum::extract::{Extension, Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use bcs_dist_model::*;
use bcs_service_api::application::dist_gateway::CreationSource;
use bcs_service_api::application::dist_gateway::*;
use futures::StreamExt;
use serde::de::DeserializeOwned;
use serde_json::Value;
use std::num::NonZeroU32;

const VERSION_HEADER: &str = "a2a-version";
const LAST_EVENT_ID: &str = "last-event-id";

/// What a method answers with: one JSON result, or a stream.
enum Answer {
    Result(Value),
    Stream(Response),
}

type Outcome = Result<Answer, A2AError>;

fn gateway(error: GatewayError) -> A2AError {
    a2a_error(&error)
}

fn params<T: DeserializeOwned>(request: &JsonRpcRequest) -> Result<T, A2AError> {
    let value = request.params.clone().unwrap_or(Value::Null);
    serde_json::from_value(value).map_err(|e| A2AError::invalid_params(format!("invalid params: {e}")))
}

fn result<T: serde::Serialize>(value: &T) -> Outcome {
    serde_json::to_value(value)
        .map(Answer::Result)
        .map_err(|_| A2AError::internal("response encoding failed"))
}

fn error_response(status: StatusCode, id: JsonRpcId, error: &A2AError) -> Response {
    (status, Json(JsonRpcResponse::error(id, error.to_jsonrpc_error()))).into_response()
}

pub(crate) async fn handle(
    State(state): State<AppState>,
    Path(group): Path<String>,
    caller: Option<Extension<GatewayCaller>>,
    headers: HeaderMap,
    body: Result<Json<JsonRpcRequest>, JsonRejection>,
) -> Response {
    let request = match body {
        Ok(Json(request)) => request,
        Err(rejection) => {
            let error = A2AError::parse_error(rejection.body_text());
            return error_response(StatusCode::OK, JsonRpcId::Null, &error);
        }
    };
    let id = request.id.clone();
    let Some(Extension(caller)) = caller else {
        let error = A2AError::invalid_request("unauthenticated");
        return error_response(StatusCode::UNAUTHORIZED, id, &error);
    };
    if request.jsonrpc != "2.0" {
        return error_response(StatusCode::OK, id, &A2AError::invalid_request("jsonrpc must be \"2.0\""));
    }
    if let Some(version) = headers.get(VERSION_HEADER).and_then(|v| v.to_str().ok())
        && version != a2a::VERSION
    {
        return error_response(StatusCode::OK, id, &A2AError::version_not_supported(version));
    }
    let call = Call {
        state: &state,
        caller: &caller,
        group: GroupId(group),
        id: id.clone(),
    };
    let outcome = match request.method.as_str() {
        methods::SEND_MESSAGE => call.send(&request).await,
        methods::SEND_STREAMING_MESSAGE => call.send_streaming(&request).await,
        methods::GET_TASK => call.get(&request).await,
        methods::SUBSCRIBE_TO_TASK => call.subscribe(&request, &headers).await,
        methods::CANCEL_TASK => call.cancel(&request).await,
        methods::LIST_TASKS => call.list(&request).await,
        methods::CREATE_PUSH_CONFIG
        | methods::GET_PUSH_CONFIG
        | methods::LIST_PUSH_CONFIGS
        | methods::DELETE_PUSH_CONFIG => Err(A2AError::push_notification_not_supported()),
        methods::GET_EXTENDED_AGENT_CARD => Err(A2AError::extended_card_not_configured()),
        other => Err(A2AError::method_not_found(other)),
    };
    match outcome {
        Ok(Answer::Result(value)) => Json(JsonRpcResponse::success(id, value)).into_response(),
        Ok(Answer::Stream(response)) => response,
        Err(error) => error_response(StatusCode::OK, id, &error),
    }
}

/// Ends a stream after a snapshot that shows the task asking for input or
/// authorization. A stream ends once the task asks for input; a snapshot can
/// carry that state instead of an event, at the start or when it is re-sent
/// after the task moved on, and then nothing further would arrive until
/// someone answers.
///
/// `answered` is set for a stream started by a reply: the coverage the task
/// showed before the reply was sent. A snapshot at that coverage still shows
/// the question just answered and keeps the stream open; a newer one is the
/// next question and ends it.
fn end_on_waiting_snapshot(
    items: TaskSubscription,
    answered: Option<Option<EventId>>,
) -> TaskSubscription {
    // Checked before the next item is awaited: once ended, the stream closes
    // without waiting for an item that would never come.
    futures::stream::unfold((items, false), move |(mut items, ended)| async move {
        if ended {
            return None;
        }
        let item = items.next().await?;
        let ended = match &item {
            Ok(TaskStreamItem::Snapshot { view, .. }) => {
                let waiting = matches!(
                    view.content.status,
                    TaskStatus::InputRequired | TaskStatus::AuthRequired
                );
                let old_question = answered.is_some_and(|before| view.covered_through <= before);
                waiting && !old_question
            }
            _ => false,
        };
        Some((item, (items, ended)))
    })
    .boxed()
}

struct Call<'a> {
    state: &'a AppState,
    caller: &'a GatewayCaller,
    group: GroupId,
    id: JsonRpcId,
}

/// The business content of a message: everything but its routing ids, so a
/// retry that adds the context id it learned is still the same content.
fn content_of(message: &Message) -> Result<Payload, A2AError> {
    let mut value =
        serde_json::to_value(message).map_err(|_| A2AError::invalid_params("message is not encodable"))?;
    if let Some(object) = value.as_object_mut() {
        for routing in ["messageId", "contextId", "taskId"] {
            object.remove(routing);
        }
    }
    Ok(Payload::Inline(value))
}

impl Call<'_> {
    fn submit_input(&self, request: &SendMessageRequest) -> Result<SubmitInput, A2AError> {
        if request
            .configuration
            .as_ref()
            .is_some_and(|c| c.task_push_notification_config.is_some())
        {
            return Err(A2AError::push_notification_not_supported());
        }
        let message = &request.message;
        Ok(SubmitInput {
            source: CreationSource::Rpc,
            group: self.group.clone(),
            message_id: RequestId(message.message_id.clone()),
            session: message.context_id.clone().map(Sid),
            task: message.task_id.clone().map(Tid),
            interaction: None,
            content: content_of(message)?,
        })
    }

    async fn send(&self, request: &JsonRpcRequest) -> Outcome {
        let request: SendMessageRequest = params(request)?;
        let input = self.submit_input(&request)?;
        let gw = &self.state.gateway;
        // A message to an existing task answers its open question: remember what
        // the task showed before, so the wait does not stop on that question.
        let before = match &input.task {
            Some(tid) => gw.get_task(self.caller, &self.group, tid, CreationSource::Rpc).await.map_err(gateway)?.covered_through,
            None => None,
        };
        let receipt = gw.submit(self.caller, input).await.map_err(gateway)?;
        let configuration = request.configuration.as_ref();
        let view = if configuration.and_then(|c| c.return_immediately) == Some(true) {
            gw.get_task(self.caller, &self.group, &receipt.tid, CreationSource::Rpc).await
        } else {
            let wait = WaitTask {
                group: self.group.clone(),
                tid: receipt.tid.clone(),
                until: WaitUntil::InputOrFinal,
                after: before,
                timeout: self.state.config.blocking_send_wait,
                source: CreationSource::Rpc,
            };
            gw.wait_task(self.caller, wait).await
        }
        .map_err(gateway)?;
        let shape = TaskShape::history(configuration.and_then(|c| c.history_length));
        result(&SendMessageResponse::Task(mapping::task(&view, shape)))
    }

    async fn send_streaming(&self, request: &JsonRpcRequest) -> Outcome {
        let request: SendMessageRequest = params(request)?;
        let input = self.submit_input(&request)?;
        let gw = &self.state.gateway;
        // A message to an existing task is the reply to its question. Note what
        // the task showed before: its stream stays open while a snapshot still
        // shows that question, and ends on a newer one.
        let answered = match &input.task {
            Some(tid) => Some(
                gw.get_task(self.caller, &self.group, tid, CreationSource::Rpc)
                    .await
                    .map_err(gateway)?
                    .covered_through,
            ),
            None => None,
        };
        let receipt = gw.submit(self.caller, input).await.map_err(gateway)?;
        let subscribe = SubscribeTask {
            group: self.group.clone(),
            tid: receipt.tid,
            mode: SubscribeMode::Snapshot,
            source: CreationSource::Rpc,
        };
        let items = gw.subscribe(self.caller, subscribe).await.map_err(gateway)?;
        Ok(Answer::Stream(stream::response(
            self.id.clone(),
            Vec::new(),
            end_on_waiting_snapshot(items, answered),
            &self.state.config,
        )))
    }

    async fn get(&self, request: &JsonRpcRequest) -> Outcome {
        let request: GetTaskRequest = params(request)?;
        let view = self
            .state
            .gateway
            .get_task(self.caller, &self.group, &Tid(request.id), CreationSource::Rpc)
            .await
            .map_err(gateway)?;
        result(&mapping::task(&view, TaskShape::history(request.history_length)))
    }

    async fn subscribe(&self, request: &JsonRpcRequest, headers: &HeaderMap) -> Outcome {
        let request: SubscribeToTaskRequest = params(request)?;
        let tid = Tid(request.id);
        // A usable Last-Event-ID resumes right after it; anything else (absent,
        // undecodable, another task's) gets the snapshot first.
        let mode = headers
            .get(LAST_EVENT_ID)
            .and_then(|v| v.to_str().ok())
            .and_then(cursor::decode)
            .filter(|cursor| cursor.tid == tid)
            .map_or(SubscribeMode::Snapshot, SubscribeMode::Resume);
        let subscribe = SubscribeTask {
            group: self.group.clone(),
            tid,
            mode,
            source: CreationSource::Rpc,
        };
        let items = self
            .state
            .gateway
            .subscribe(self.caller, subscribe)
            .await
            .map_err(gateway)?;
        let mut items = end_on_waiting_snapshot(items, None);
        let first = items.next().await;
        // A2A 1.0: a task in a terminal state cannot be subscribed to; the
        // client reads its final state with GetTask.
        if let Some(Ok(TaskStreamItem::Snapshot { view, .. })) = &first
            && mapping::state(view.content.status).is_terminal()
        {
            return Err(A2AError::unsupported_operation(
                "task is in a terminal state; use GetTask",
            ));
        }
        Ok(Answer::Stream(stream::response(
            self.id.clone(),
            first.into_iter().collect(),
            items,
            &self.state.config,
        )))
    }

    async fn cancel(&self, request: &JsonRpcRequest) -> Outcome {
        let request: CancelTaskRequest = params(request)?;
        let tid = Tid(request.id);
        // The JSON-RPC request id identifies this cancel: a retry repeats it.
        let request_id = match &self.id {
            JsonRpcId::String(id) => id.clone(),
            JsonRpcId::Number(id) => id.to_string(),
            JsonRpcId::Null => "cancel".to_owned(),
        };
        let gw = &self.state.gateway;
        let cancel = CancelTask {
            group: self.group.clone(),
            tid: tid.clone(),
            request_id: RequestId(request_id),
            reason: None,
            source: CreationSource::Rpc,
        };
        gw.cancel(self.caller, cancel).await.map_err(gateway)?;
        let wait = WaitTask {
            group: self.group.clone(),
            tid,
            until: WaitUntil::Final,
            after: None,
            timeout: self.state.config.cancel_wait,
            source: CreationSource::Rpc,
        };
        let view = gw.wait_task(self.caller, wait).await.map_err(gateway)?;
        result(&mapping::task(&view, TaskShape::FULL))
    }

    async fn list(&self, request: &JsonRpcRequest) -> Outcome {
        let request: ListTasksRequest = params(request)?;
        let size = match request.page_size {
            None => self.state.config.default_page_size,
            Some(size) => u32::try_from(size).map_err(|_| A2AError::invalid_params("pageSize must be positive"))?,
        };
        let limit = NonZeroU32::new(size).ok_or_else(|| A2AError::invalid_params("pageSize must be positive"))?;
        let list = ListTasks {
            group: self.group.clone(),
            session: request.context_id.map(Sid),
            statuses: request.status.as_ref().and_then(mapping::status_filter).into_iter().collect(),
            after: request.page_token.filter(|token| !token.is_empty()).map(PageToken),
            limit,
        };
        let page = self.state.gateway.list_tasks(self.caller, list).await.map_err(gateway)?;
        let shape = TaskShape {
            history_length: Some(request.history_length.map_or(0, |n| usize::try_from(n).unwrap_or(0))),
            artifacts: request.include_artifacts == Some(true),
        };
        let tasks: Vec<_> = page.tasks.iter().map(|view| mapping::task(view, shape)).collect();
        let count = i32::try_from(tasks.len()).unwrap_or(i32::MAX);
        result(&ListTasksResponse {
            tasks,
            next_page_token: page.next.map(|token| token.0).unwrap_or_default(),
            page_size: count,
            // The index is not counted; only this page's size is known.
            total_size: count,
        })
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used, reason = "test assertions")]
    use super::*;
    use std::num::NonZeroU32;

    fn eid(commit: u64) -> EventId {
        EventId {
            commit: CommitSeq(commit),
            index: NonZeroU32::MIN,
        }
    }

    fn snapshot(status: TaskStatus, covered: u64) -> Result<TaskStreamItem, GatewayError> {
        Ok(TaskStreamItem::Snapshot {
            view: TaskView {
                scope: TaskScope {
                    tenant: TenantId("t".into()),
                    principal: PrincipalId("p".into()),
                },
                sid: Sid("s0001.a".into()),
                tid: Tid("s0001.a.b".into()),
                content: TaskContent {
                    status,
                    interactions: Vec::new(),
                    snapshot: Payload::Inline(Value::Null),
                    result_ref: None,
                },
                covered_through: Some(eid(covered)),
            },
            cursor: None,
        })
    }

    /// How many items get through, of a stream that would never end by itself.
    async fn delivered(
        items: Vec<Result<TaskStreamItem, GatewayError>>,
        answered: Option<Option<EventId>>,
    ) -> usize {
        let endless = futures::stream::iter(items).chain(futures::stream::pending()).boxed();
        let ended = end_on_waiting_snapshot(endless, answered).collect::<Vec<_>>();
        tokio::time::timeout(std::time::Duration::from_secs(2), ended)
            .await
            .map(|items| items.len())
            .unwrap_or(usize::MAX)
    }

    #[tokio::test]
    async fn a_waiting_snapshot_ends_a_stream_wherever_it_comes() {
        // First item.
        assert_eq!(delivered(vec![snapshot(TaskStatus::InputRequired, 6)], None).await, 1);
        // Re-sent after the task moved on (a gap): the question is in the
        // snapshot, not in an event, and still ends the stream.
        let resent = vec![
            snapshot(TaskStatus::Working, 5),
            snapshot(TaskStatus::AuthRequired, 6),
            snapshot(TaskStatus::Working, 9),
        ];
        assert_eq!(delivered(resent, None).await, 2);
    }

    #[tokio::test]
    async fn a_reply_stream_stays_open_on_the_question_it_answers_only() {
        let answered = Some(Some(eid(7)));
        // The task still shows the answered question, then goes on: stays open
        // (never ends by itself here, so nothing is "delivered and closed").
        let old = vec![snapshot(TaskStatus::InputRequired, 7), snapshot(TaskStatus::Working, 8)];
        assert_eq!(delivered(old, answered).await, usize::MAX);
        // The Worker was fast: the first snapshot already shows the next question.
        assert_eq!(delivered(vec![snapshot(TaskStatus::InputRequired, 9)], answered).await, 1);
        // The old question, then the next one in a re-sent snapshot.
        let both = vec![snapshot(TaskStatus::InputRequired, 7), snapshot(TaskStatus::InputRequired, 9)];
        assert_eq!(delivered(both, answered).await, 2);
    }
}
