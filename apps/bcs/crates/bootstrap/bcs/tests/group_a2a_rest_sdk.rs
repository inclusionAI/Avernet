//! Real REST client + pinned A2A SDK models against the mounted group router.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "SDK interoperability assertions")]
mod group_a2a_companion_support;

use a2a_e2e_sdk::{
    GetTaskRequest, Message, Part, Role, SendMessageConfiguration, SendMessageRequest,
    SendMessageResponse, StreamResponse, SubscribeToTaskRequest, Task, TaskState,
};
use bcs_a2a_http::client::{RestClient, bind_rest_client};
use bcs_dist_gateway_http::GROUPS_PREFIX;
use bcs_dist_model::{Sid, Tid};
use bcs_store_api::{WriteOutcome, document::DocumentStorePlugin};
use futures::StreamExt;
use serde_json::json;
use group_a2a_companion_support::*;
use std::sync::atomic::Ordering;
use std::time::Duration;
use tokio::task::JoinHandle;

struct MountedGroup {
    client: RestClient,
    server: JoinHandle<()>,
}

impl MountedGroup {
    async fn start(world: &CompanionWorld) -> Self {
        let app = world
            .assembly
            .router
            .clone()
            .layer(axum::extract::Extension(caller()));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        let http = reqwest_e2e::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(10))
            .build()
            .unwrap();
        let client = bind_rest_client(http, format!("http://{addr}{GROUPS_PREFIX}/{GROUP}"), None);
        Self { client, server }
    }
}

impl Drop for MountedGroup {
    fn drop(&mut self) {
        self.server.abort();
    }
}

fn send_request(id: &str) -> SendMessageRequest {
    let mut message = Message::new(Role::User, vec![Part::text("research this")]);
    message.message_id = id.to_owned();
    SendMessageRequest {
        message,
        configuration: Some(SendMessageConfiguration {
            accepted_output_modes: Some(vec!["text/plain".to_owned()]),
            task_push_notification_config: None,
            history_length: Some(1),
            return_immediately: Some(true),
        }),
        metadata: None,
        tenant: None,
    }
}

#[tokio::test]
async fn rest_sdk_sends_then_gets_and_post_subscribes_to_the_same_task() {
    let world = CompanionWorld::assemble(enabled_config()).await;
    let mounted = MountedGroup::start(&world).await;
    let client = &mounted.client;

    let response = client.send_message(&send_request("sdk-send")).await.unwrap();
    let SendMessageResponse::Task(task) = response else {
        panic!("group send must return the SDK's Task variant: {response:?}");
    };
    assert_eq!(task.status.state, TaskState::Submitted);
    let got = client.get_task(&GetTaskRequest {
        id: task.id.clone(),
        history_length: Some(0),
        tenant: None,
    }).await.unwrap();
    assert_same_task(&got, &task);

    let mut stream = client.subscribe_to_task(&SubscribeToTaskRequest {
        id: task.id.clone(),
        tenant: None,
    }).await.unwrap();
    let first = stream.next().await.expect("initial SSE frame").unwrap();
    let StreamResponse::Task(snapshot) = first else {
        panic!("POST subscribe must start with the SDK Task variant: {first:?}");
    };
    assert_same_task(&snapshot, &task);
}

#[tokio::test]
async fn rest_sdk_message_stream_decodes_task_status_and_artifact_updates() {
    let world = CompanionWorld::assemble(enabled_config()).await;
    let mounted = MountedGroup::start(&world).await;
    let mut request = send_request("sdk-stream");
    request.configuration = None;
    let mut stream = mounted.client.send_streaming_message(&request).await.unwrap();
    let first = stream.next().await.expect("initial SSE frame").unwrap();
    let StreamResponse::Task(task) = first else {
        panic!("message:stream must start with the SDK Task variant: {first:?}");
    };
    assert!(!task.id.is_empty());
    assert!(!task.context_id.is_empty());
    assert_eq!(task.status.state, TaskState::Submitted);

    let connector = world.start_connector();
    let run = drive_round(&world);
    let receive = async {
        let mut working = false;
        let mut artifact = false;
        let mut completed = false;
        while let Some(frame) = stream.next().await {
            let frame = match frame {
                Ok(frame) => frame,
                Err(error) => panic!(
                    "subscription failed with {error:?}; working={working}, artifact={artifact}, completed={completed}, starts={}, executions={}, latest get={:?}",
                    world.journal.starts_seen.load(Ordering::SeqCst),
                    world.journal.executions.load(Ordering::SeqCst),
                    mounted.client.get_task(&GetTaskRequest {
                        id: task.id.clone(),
                        history_length: Some(0),
                        tenant: None,
                    }).await,
                ),
            };
            match frame {
                StreamResponse::StatusUpdate(update) if !completed => {
                    assert_eq!(update.task_id, task.id);
                    assert_eq!(update.context_id, task.context_id);
                    match update.status.state {
                        TaskState::Working => working = true,
                        TaskState::Completed => {
                            assert!(artifact, "the artifact must precede completion");
                            completed = true;
                        }
                        state => panic!("unexpected group status: {state:?}"),
                    }
                }
                StreamResponse::ArtifactUpdate(update) if !artifact && !completed => {
                    assert_eq!(update.task_id, task.id);
                    assert_eq!(update.context_id, task.context_id);
                    assert_eq!(update.artifact.artifact_id, "review-answer");
                    assert_eq!(update.artifact.parts, [Part::text("answer-of-review")]);
                    artifact = true;
                }
                other => panic!("unexpected group frame: {other:?}"),
            }
        }
        assert!(working && artifact && completed, "missing group stream updates");
    };
    let result = tokio::time::timeout(Duration::from_secs(10), async {
        tokio::select! {
            () = run => unreachable!("the world drives until the client finishes"),
            () = receive => {}
        }
    }).await;
    connector.abort();
    result.expect("group round and stream close within the test budget");

    // Subscribing to the terminal task returns its final Task snapshot and EOF.
    let mut stream = mounted.client.subscribe_to_task(&SubscribeToTaskRequest {
        id: task.id.clone(),
        tenant: None,
    }).await.unwrap();
    let first = stream.next().await.expect("terminal SSE snapshot").unwrap();
    let StreamResponse::Task(snapshot) = first else {
        panic!("terminal POST subscribe must return a Task snapshot: {first:?}");
    };
    assert_eq!(snapshot.id, task.id);
    assert_eq!(snapshot.context_id, task.context_id);
    assert_eq!(snapshot.status.state, TaskState::Completed);
    assert!(stream.next().await.is_none());
}

#[tokio::test]
async fn rest_sdk_never_exposes_final_artifact_before_the_authority_cut() {
    let world = CompanionWorld::assemble(enabled_config()).await;
    let mounted = MountedGroup::start(&world).await;
    let client = &mounted.client;
    let mut stream = client.send_streaming_message(&send_request("sdk-unconfirmed"))
        .await.unwrap();
    let first = stream.next().await.expect("initial SSE frame").unwrap();
    let StreamResponse::Task(task) = first else {
        panic!("message:stream must start with a Task: {first:?}");
    };

    // The real strict terminal CAS verifies its closure digest. Install
    // conflicting evidence to keep the authority unconfirmed even after
    // the committed feed has published the round's final answer.
    let tid = Tid(task.id.clone());
    let sid = Sid(task.context_id.clone());
    assert!(matches!(world.model.create_document(
        &format!("closure:{}", task.id), &json!({"stopped": false})
    ).await.unwrap(), WriteOutcome::Applied { .. }));
    let connector = world.start_connector();
    let receive = async {
        loop {
            match stream.next().await.expect("a closed authority window must report an error") {
                Ok(StreamResponse::StatusUpdate(update)) => {
                    assert_eq!(update.status.state, TaskState::Working);
                }
                Err(error) => {
                    // The repository client currently maps custom terminal
                    // error data to a decode error, not a typed SDK frame.
                    assert_eq!(error.message, "SSE contains invalid StreamResponse");
                    break;
                }
                Ok(frame) => panic!("unconfirmed terminal content escaped: {frame:?}"),
            }
        }
    };
    let result = tokio::time::timeout(Duration::from_secs(10), async {
        tokio::select! {
            () = drive_round(&world) => unreachable!("drive waits for the stream"),
            () = receive => {}
        }
    }).await;
    connector.abort();
    result.expect("the stalled authority must fail closed within the test budget");

    // The real terminal snapshot carries the artifact; simulate the valid
    // ordering where the publication shows the finalizing Working view
    // first (artifact in the display, but no terminal state or result ref).
    let layout = &world.assembly.roles.layout;
    let key = layout.task(&tid).unwrap();
    let published = world.model.read_document(&key).await.unwrap().unwrap();
    assert!(published.value.to_string().contains("review-answer"));
    let ckpt = world.model.read_document(&layout.ckpt(&sid).unwrap())
        .await.unwrap().unwrap().value;
    let finalizing = ckpt["checkpoint"]["tasks"].as_array().unwrap().iter()
        .find(|entry| entry["tid"] == task.id).unwrap();
    let mut pending = published.value;
    pending["content"]["status"] = json!("working");
    pending["content"]["result_ref"] = serde_json::Value::Null;
    pending["source_commit"] = finalizing["finalization"]["record"]["source_commit"].clone();
    assert!(pending["source_commit"].is_u64());
    assert!(matches!(world.model.replace_document(&key, published.version, &pending)
        .await.unwrap(), WriteOutcome::Applied { .. }));

    let got = client.get_task(&GetTaskRequest { id: task.id, history_length: None, tenant: None })
        .await.unwrap();
    assert_eq!(got.status.state, TaskState::Working);
    assert!(got.artifacts.is_none_or(|artifacts| artifacts.is_empty()));
}

async fn drive_round(world: &CompanionWorld) {
    for (starts, node) in [(1, NODE_RESEARCH), (2, NODE_REVIEW)] {
        while world.journal.executions.load(Ordering::SeqCst) < starts {
            world.drive(1).await;
        }
        world.journal.finish(node);
    }
    // Keep driving publication until the observing future returns.
    loop {
        world.drive(1).await;
    }
}

fn assert_same_task(actual: &Task, expected: &Task) {
    assert_eq!(actual.id, expected.id);
    assert_eq!(actual.context_id, expected.context_id);
    assert_eq!(actual.status.state, expected.status.state);
}
