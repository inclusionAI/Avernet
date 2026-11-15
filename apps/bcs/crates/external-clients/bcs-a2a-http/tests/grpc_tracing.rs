//! Exercise tracing through the real gRPC binding, including wire metadata.
#![allow(clippy::unwrap_used, clippy::expect_used)]
use std::sync::{Arc, Mutex};
use a2a_pb::proto::{self as pb, a2a_service_server::{A2aService, A2aServiceServer}};
use bcs_a2a_http::grpc_client::GrpcClient;
use bcs_tracing_api::{Carrier, Kind, NetworkTracer, Outcome, ParentContext, Record, RequestInfo, StartedTrace};
use futures::{stream::BoxStream, StreamExt};
use tonic::{Request, Response, Status};

const METHODS: [&str; 5] = ["SendMessage", "SendStreamingMessage", "GetTask", "CancelTask", "SubscribeToTask"];

#[derive(Default)]
struct Spy {
    starts: Mutex<Vec<(String, Kind)>>,
    finishes: Mutex<Vec<Outcome>>,
}
struct Provider(Arc<Spy>);
struct Completion(Arc<Spy>);
impl Record for Completion {
    fn finish(self: Box<Self>, outcome: Outcome, size: Option<u64>) {
        assert_eq!(size, None);
        self.0.finishes.lock().unwrap().push(outcome);
    }
}
impl NetworkTracer for Provider {
    fn start(&self, info: &RequestInfo<'_>, incoming: &Carrier, parent: ParentContext<'_>) -> StartedTrace {
        assert_eq!(info.endpoint, "fixture.A2AService");
        assert_eq!(info.target_app, "fixture-agent");
        assert_eq!(parent.local.unwrap().downcast_ref::<String>().unwrap(), "parent");
        assert!(incoming.contains(&("x-trace".into(), "stale".into())));
        assert!(!incoming.iter().any(|(key, _)| key == "binary-bin"));
        self.0.starts.lock().unwrap().push((info.method.into(), info.kind));
        StartedTrace {
            context: Arc::new(String::from("child")),
            remove_headers: vec!["x-trace".into(), "invalid header".into()],
            headers: vec![("x-trace".into(), "child".into()), ("invalid header".into(), "ignored".into()),
                ("x-invalid-value".into(), "invalid\r\nvalue".into())],
            record: Box::new(Completion(self.0.clone())),
        }
    }
}

struct Peer { fail: bool, methods: Arc<Mutex<Vec<String>>> }
impl Peer {
    fn receive<T>(&self, request: &Request<T>, tenant: &str, method: &str) -> Result<(), Status> {
        let metadata = request.metadata();
        assert_eq!(tenant, "fixture-tenant");
        assert_eq!(metadata.get("sofa_head_method_name").unwrap(), method);
        assert_eq!(metadata.get("authorization").unwrap(), "Bearer fixture");
        assert_eq!(metadata.get("a2a-version").unwrap(), "1.0");
        assert_eq!(metadata.get("x-trace").unwrap(), "child");
        assert!(metadata.get_bin("binary-bin").is_some());
        assert!(metadata.get("grpc-timeout").is_some());
        assert!(metadata.get("x-invalid-value").is_none());
        self.methods.lock().unwrap().push(method.into());
        if self.fail { Err(Status::unavailable("private peer diagnostic")) } else { Ok(()) }
    }
}
fn task() -> pb::Task {
    pb::Task { id: "task".into(), context_id: "context".into(), status: Some(pb::TaskStatus {
        state: pb::TaskState::Completed as i32, ..Default::default()
    }), ..Default::default() }
}
fn events() -> BoxStream<'static, Result<pb::StreamResponse, Status>> {
    futures::stream::iter([Ok(pb::StreamResponse {
        payload: Some(pb::stream_response::Payload::Task(task())),
    })]).boxed()
}
#[tonic::async_trait]
impl A2aService for Peer {
    async fn send_message(&self, request: Request<pb::SendMessageRequest>) -> Result<Response<pb::SendMessageResponse>, Status> {
        self.receive(&request, &request.get_ref().tenant, METHODS[0])?;
        Ok(Response::new(pb::SendMessageResponse { payload: Some(pb::send_message_response::Payload::Task(task())) }))
    }
    type SendStreamingMessageStream = BoxStream<'static, Result<pb::StreamResponse, Status>>;
    async fn send_streaming_message(&self, request: Request<pb::SendMessageRequest>) -> Result<Response<Self::SendStreamingMessageStream>, Status> {
        self.receive(&request, &request.get_ref().tenant, METHODS[1])?;
        Ok(Response::new(events()))
    }
    async fn get_task(&self, request: Request<pb::GetTaskRequest>) -> Result<Response<pb::Task>, Status> {
        self.receive(&request, &request.get_ref().tenant, METHODS[2])?;
        Ok(Response::new(task()))
    }
    async fn cancel_task(&self, request: Request<pb::CancelTaskRequest>) -> Result<Response<pb::Task>, Status> {
        self.receive(&request, &request.get_ref().tenant, METHODS[3])?;
        Ok(Response::new(task()))
    }
    type SubscribeToTaskStream = BoxStream<'static, Result<pb::StreamResponse, Status>>;
    async fn subscribe_to_task(&self, request: Request<pb::SubscribeToTaskRequest>) -> Result<Response<Self::SubscribeToTaskStream>, Status> {
        self.receive(&request, &request.get_ref().tenant, METHODS[4])?;
        Ok(Response::new(events()))
    }
    async fn list_tasks(&self, _: Request<pb::ListTasksRequest>) -> Result<Response<pb::ListTasksResponse>, Status> {
        Err(Status::unimplemented("unused fixture method"))
    }
    async fn create_task_push_notification_config(&self, _: Request<pb::TaskPushNotificationConfig>) -> Result<Response<pb::TaskPushNotificationConfig>, Status> {
        Err(Status::unimplemented("unused fixture method"))
    }
    async fn get_task_push_notification_config(&self, _: Request<pb::GetTaskPushNotificationConfigRequest>) -> Result<Response<pb::TaskPushNotificationConfig>, Status> {
        Err(Status::unimplemented("unused fixture method"))
    }
    async fn list_task_push_notification_configs(&self, _: Request<pb::ListTaskPushNotificationConfigsRequest>) -> Result<Response<pb::ListTaskPushNotificationConfigsResponse>, Status> {
        Err(Status::unimplemented("unused fixture method"))
    }
    async fn get_extended_agent_card(&self, _: Request<pb::GetExtendedAgentCardRequest>) -> Result<Response<pb::AgentCard>, Status> {
        Err(Status::unimplemented("unused fixture method"))
    }
    async fn delete_task_push_notification_config(&self, _: Request<pb::DeleteTaskPushNotificationConfigRequest>) -> Result<Response<()>, Status> {
        Err(Status::unimplemented("unused fixture method"))
    }
}

struct Server(tokio::task::JoinHandle<()>);
impl Drop for Server { fn drop(&mut self) { self.0.abort(); } }

async fn exercise(fail: bool) {
    let methods = Arc::new(Mutex::new(Vec::new()));
    let peer = Peer { fail, methods: methods.clone() };
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let incoming = futures::stream::unfold(listener, |listener| async move {
        let accepted = listener.accept().await.map(|(stream, _)| stream);
        Some((accepted, listener))
    });
    let _server = Server(tokio::spawn(async move {
        tonic::transport::Server::builder().add_service(A2aServiceServer::new(peer))
            .serve_with_incoming(incoming).await.unwrap();
    }));
    let channel = tonic::transport::Endpoint::from_shared(endpoint).unwrap().connect().await.unwrap();
    let mut headers = tonic::metadata::MetadataMap::new();
    for (key, value) in [("authorization", "Bearer fixture"), ("sofa_head_target_app", "fixture-agent"),
        ("sofa_head_target_service", "fixture.A2AService"), ("x-trace", "stale")] {
        headers.insert(key, value.parse().unwrap());
    }
    headers.insert_bin("binary-bin", tonic::metadata::MetadataValue::from_bytes(b"opaque"));
    let client = GrpcClient::new(channel, headers, Some("fixture-tenant".into()))
        .with_timeout(std::time::Duration::from_secs(5));
    let spy = Arc::new(Spy::default());
    let message = serde_json::from_value(serde_json::json!({"message": {
        "messageId": "message", "role": "ROLE_USER", "parts": [{"text": "fixture"}]
    }})).unwrap();
    let query = serde_json::json!({"id": "task"});
    bcs_tracing_api::with_provider(Some(Arc::new(Provider(spy.clone()))),
        bcs_tracing_api::with_context(Some(Arc::new(String::from("parent"))), async {
            let send = client.send_message(&message).await.map(|response| match response {
                a2a::SendMessageResponse::Task(task) => assert_eq!(task.id, "task"),
                _ => panic!("fixture returned a Task"),
            });
            let stream = client.send_streaming_message(&message).await;
            let get = client.get_task(&serde_json::from_value(query.clone()).unwrap()).await
                .map(|task| assert_eq!(task.id, "task"));
            let cancel = client.cancel_task(&serde_json::from_value(query.clone()).unwrap()).await
                .map(|task| assert_eq!(task.id, "task"));
            let subscription = client.subscribe_to_task(&serde_json::from_value(query).unwrap()).await;
            assert_eq!(bcs_tracing_api::current_context().unwrap().downcast_ref::<String>().unwrap(), "parent");
            // Traces finish at response readiness; reading streams adds no records.
            assert_eq!(spy.finishes.lock().unwrap().len(), 5);
            let mut results = vec![send, get, cancel];
            for result in [stream, subscription] {
                results.push(match result {
                    Ok(mut stream) => {
                        let a2a::StreamResponse::Task(task) = stream.next().await.unwrap().unwrap() else { panic!("Task event required"); };
                        assert_eq!(task.id, "task");
                        assert!(stream.next().await.is_none());
                        Ok(())
                    }
                    Err(error) => Err(error),
                });
            }
            for result in results {
                if fail {
                    let error = result.unwrap_err();
                    assert_eq!(error.message, "A2A gRPC 14");
                    assert!(!format!("{error:?}").contains("private peer diagnostic"));
                } else { result.unwrap(); }
            }
        })).await;
    assert!(bcs_tracing_api::current_context().is_none());
    assert_eq!(*methods.lock().unwrap(), METHODS);
    let expected: Vec<_> = METHODS.into_iter().enumerate().map(|(index, method)| (
        method.into(), Kind::GrpcClient { server_streaming: matches!(index, 1 | 4) },
    )).collect();
    assert_eq!(*spy.starts.lock().unwrap(), expected);
    assert_eq!(*spy.finishes.lock().unwrap(), vec![Outcome::Rpc(if fail { 14 } else { 0 }); 5]);
}

#[tokio::test]
async fn all_operations_preserve_wire_metadata_models_and_trace_shape() { exercise(false).await; }

#[tokio::test]
async fn all_operations_preserve_raw_grpc_failure_status_and_redact_peer_details() { exercise(true).await; }
