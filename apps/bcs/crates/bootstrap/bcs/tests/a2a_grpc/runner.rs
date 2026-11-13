//! External gRPC peer for the live A2A story. Translate only at the mock's
//! boundary; BCS uses its production gRPC mesh routing and client unchanged.
use a2a_e2e_sdk as a2a;
use a2a_pb_e2e::{
    pbconv,
    proto::{
        self as pb,
        a2a_service_server::{A2aService, A2aServiceServer},
    },
};
use futures::{StreamExt, stream::BoxStream};
use serde::{Serialize, de::DeserializeOwned};
use serde_json::{Value, json};
use tonic_e2e::{Request, Response, Status};

#[derive(clap::Parser)]
struct Args {
    #[arg(long)]
    upstream: String,
    #[arg(long)]
    ready_file: std::path::PathBuf,
}

#[derive(Clone)]
struct Peer {
    upstream: String,
    http: reqwest_e2e::Client,
}

impl Peer {
    async fn call<T: Serialize>(
        &self,
        method: &str,
        params: T,
        metadata: &tonic_e2e::metadata::MetadataMap,
    ) -> Result<reqwest_e2e::Response, Status> {
        let mut request = self.http.post(&self.upstream).header("a2a-version", "1.0");
        if let Some(extensions) = metadata.get("a2a-extensions") {
            request = request.header(
                "a2a-extensions",
                extensions
                    .to_str()
                    .map_err(|_| Status::invalid_argument("invalid extensions"))?,
            );
        }
        request
            .json(
                &json!({"jsonrpc": "2.0", "id": uuid::Uuid::new_v4().to_string(),
                "method": method, "params": params}),
            )
            .send()
            .await
            .and_then(reqwest_e2e::Response::error_for_status)
            .map_err(|_| Status::unavailable("scripted peer unavailable"))
    }

    async fn unary<T: Serialize, R: DeserializeOwned>(
        &self,
        method: &str,
        params: T,
        metadata: &tonic_e2e::metadata::MetadataMap,
    ) -> Result<R, Status> {
        let body: Value = self
            .call(method, params, metadata)
            .await?
            .json()
            .await
            .map_err(|_| Status::internal("invalid peer JSON"))?;
        decode(body)
    }

    async fn events<T: Serialize>(
        &self,
        method: &str,
        params: T,
        metadata: &tonic_e2e::metadata::MetadataMap,
    ) -> Result<BoxStream<'static, Result<pb::StreamResponse, Status>>, Status> {
        let response = self.call(method, params, metadata).await?;
        let chunks = response.bytes_stream().boxed();
        Ok(futures::stream::try_unfold(
            (chunks, Vec::<u8>::new()),
            |(mut chunks, mut buffer)| async move {
                loop {
                    if let Some(end) = buffer.windows(2).position(|pair| pair == b"\n\n") {
                        let frame: Vec<_> = buffer.drain(..end + 2).collect();
                        let text = std::str::from_utf8(&frame)
                            .map_err(|_| Status::internal("invalid peer UTF-8"))?;
                        let data = text
                            .lines()
                            .filter_map(|line| line.strip_prefix("data:"))
                            .collect::<Vec<_>>()
                            .join("\n");
                        if data.is_empty() {
                            continue;
                        }
                        let body = serde_json::from_str(&data)
                            .map_err(|_| Status::internal("invalid peer event"))?;
                        let event: a2a::StreamResponse = decode(body)?;
                        return Ok(Some((
                            pbconv::to_proto_stream_response(&event),
                            (chunks, buffer),
                        )));
                    }
                    match chunks.next().await {
                        Some(Ok(bytes)) => {
                            buffer.extend_from_slice(&bytes);
                            if buffer.len() > 8 * 1024 * 1024 {
                                return Err(Status::resource_exhausted("peer event too large"));
                            }
                        }
                        Some(Err(_)) => return Err(Status::unavailable("peer stream interrupted")),
                        None if buffer.is_empty() => return Ok(None),
                        None => return Err(Status::internal("incomplete peer event")),
                    }
                }
            },
        )
        .boxed())
    }
}

fn decode<T: DeserializeOwned>(mut body: Value) -> Result<T, Status> {
    let result = body
        .get_mut("result")
        .ok_or_else(|| Status::internal("peer omitted result"))?
        .take();
    serde_json::from_value(result).map_err(|_| Status::internal("invalid peer result"))
}

fn check_metadata<T>(request: &Request<T>, method: &str) -> Result<(), Status> {
    for (key, expected) in [
        ("a2a-version", "1.0"),
        ("sofa_head_method_name", method),
        ("sofa_head_target_app", "grpc-e2e"),
        ("sofa_head_target_service", "lf.a2a.v1.A2AService"),
    ] {
        if request
            .metadata()
            .get(key)
            .and_then(|value| value.to_str().ok())
            != Some(expected)
        {
            return Err(Status::invalid_argument(
                "missing deployment routing metadata",
            ));
        }
    }
    Ok(())
}

#[tonic_e2e::async_trait]
impl A2aService for Peer {
    async fn send_message(
        &self,
        request: Request<pb::SendMessageRequest>,
    ) -> Result<Response<pb::SendMessageResponse>, Status> {
        check_metadata(&request, "SendMessage")?;
        let result: a2a::SendMessageResponse = self
            .unary(
                "SendMessage",
                pbconv::from_proto_send_message_request(request.get_ref()),
                request.metadata(),
            )
            .await?;
        Ok(Response::new(pbconv::to_proto_send_message_response(
            &result,
        )))
    }

    type SendStreamingMessageStream = BoxStream<'static, Result<pb::StreamResponse, Status>>;
    async fn send_streaming_message(
        &self,
        request: Request<pb::SendMessageRequest>,
    ) -> Result<Response<Self::SendStreamingMessageStream>, Status> {
        check_metadata(&request, "SendStreamingMessage")?;
        Ok(Response::new(
            self.events(
                "SendStreamingMessage",
                pbconv::from_proto_send_message_request(request.get_ref()),
                request.metadata(),
            )
            .await?,
        ))
    }

    async fn get_task(
        &self,
        request: Request<pb::GetTaskRequest>,
    ) -> Result<Response<pb::Task>, Status> {
        check_metadata(&request, "GetTask")?;
        let task: a2a::Task = self
            .unary(
                "GetTask",
                pbconv::from_proto_get_task_request(request.get_ref()),
                request.metadata(),
            )
            .await?;
        Ok(Response::new(pbconv::to_proto_task(&task)))
    }

    async fn cancel_task(
        &self,
        request: Request<pb::CancelTaskRequest>,
    ) -> Result<Response<pb::Task>, Status> {
        check_metadata(&request, "CancelTask")?;
        let task: a2a::Task = self
            .unary(
                "CancelTask",
                pbconv::from_proto_cancel_task_request(request.get_ref()),
                request.metadata(),
            )
            .await?;
        Ok(Response::new(pbconv::to_proto_task(&task)))
    }

    type SubscribeToTaskStream = BoxStream<'static, Result<pb::StreamResponse, Status>>;
    async fn subscribe_to_task(
        &self,
        request: Request<pb::SubscribeToTaskRequest>,
    ) -> Result<Response<Self::SubscribeToTaskStream>, Status> {
        check_metadata(&request, "SubscribeToTask")?;
        Ok(Response::new(
            self.events(
                "SubscribeToTask",
                pbconv::from_proto_subscribe_to_task_request(request.get_ref()),
                request.metadata(),
            )
            .await?,
        ))
    }

    async fn list_tasks(
        &self,
        _: Request<pb::ListTasksRequest>,
    ) -> Result<Response<pb::ListTasksResponse>, Status> {
        Err(Status::unimplemented("unused fixture method"))
    }
    async fn create_task_push_notification_config(
        &self,
        _: Request<pb::TaskPushNotificationConfig>,
    ) -> Result<Response<pb::TaskPushNotificationConfig>, Status> {
        Err(Status::unimplemented("unused fixture method"))
    }
    async fn get_task_push_notification_config(
        &self,
        _: Request<pb::GetTaskPushNotificationConfigRequest>,
    ) -> Result<Response<pb::TaskPushNotificationConfig>, Status> {
        Err(Status::unimplemented("unused fixture method"))
    }
    async fn list_task_push_notification_configs(
        &self,
        _: Request<pb::ListTaskPushNotificationConfigsRequest>,
    ) -> Result<Response<pb::ListTaskPushNotificationConfigsResponse>, Status> {
        Err(Status::unimplemented("unused fixture method"))
    }
    async fn get_extended_agent_card(
        &self,
        _: Request<pb::GetExtendedAgentCardRequest>,
    ) -> Result<Response<pb::AgentCard>, Status> {
        Err(Status::unimplemented("unused fixture method"))
    }
    async fn delete_task_push_notification_config(
        &self,
        _: Request<pb::DeleteTaskPushNotificationConfigRequest>,
    ) -> Result<Response<()>, Status> {
        Err(Status::unimplemented("unused fixture method"))
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    use clap::Parser;
    let args = Args::parse();
    let url = reqwest_e2e::Url::parse(&args.upstream)?;
    anyhow::ensure!(
        url.scheme() == "http"
            && url.host_str() == Some("127.0.0.1")
            && url.port().is_some()
            && url.path() == "/a2a"
            && url.query().is_none()
            && url.fragment().is_none()
            && url.username().is_empty()
            && url.password().is_none(),
        "local mock origin required"
    );
    let peer = Peer {
        upstream: args.upstream,
        http: reqwest_e2e::Client::builder()
            .no_proxy()
            .redirect(reqwest_e2e::redirect::Policy::none())
            .timeout(std::time::Duration::from_secs(60))
            .build()?,
    };
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
    std::fs::write(
        args.ready_file,
        format!("http://{}", listener.local_addr()?),
    )?;
    let incoming = futures::stream::unfold(listener, |listener| async move {
        Some((listener.accept().await.map(|(socket, _)| socket), listener))
    });
    tonic_e2e::transport::Server::builder()
        .add_service(A2aServiceServer::new(peer))
        .serve_with_incoming_shutdown(incoming, async {
            let mut terminate =
                tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
                    .expect("SIGTERM handler");
            tokio::select! { _ = terminate.recv() => {}, _ = tokio::signal::ctrl_c() => {} }
        })
        .await?;
    Ok(())
}
