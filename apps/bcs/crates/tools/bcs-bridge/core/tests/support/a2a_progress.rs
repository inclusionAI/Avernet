//! Progress remains live over both A2A bindings without contaminating answers.
use super::support::*;
use a2a::{StreamResponse, TaskState};
use futures::{stream::BoxStream, StreamExt};
use serde_json::{json, Value};
use std::time::Duration;

struct Events {
    chunks: BoxStream<'static, Result<Vec<u8>, reqwest::Error>>,
    buffered: Vec<u8>,
    rpc: bool,
}
impl Events {
    fn new(response: reqwest::Response, rpc: bool) -> Self {
        assert!(response.status().is_success());
        Self { chunks: response.bytes_stream().map(|chunk| chunk.map(|bytes| bytes.to_vec())).boxed(), buffered: Vec::new(), rpc }
    }
    async fn next(&mut self) -> Option<StreamResponse> {
        loop {
            if let Some(end) = self.buffered.windows(2).position(|pair| pair == b"\n\n") {
                let frame: Vec<_> = self.buffered.drain(..end + 2).collect();
                let mut values = events(std::str::from_utf8(&frame).unwrap());
                if let Some(value) = values.pop() {
                    let value = if self.rpc { assert!(value.get("error").is_none(), "{value}"); value["result"].clone() } else { value };
                    return Some(serde_json::from_value(value).unwrap());
                }
            } else {
                let Some(chunk) = self.chunks.next().await else { return None; };
                self.buffered.extend(chunk.unwrap());
            }
        }
    }
}

async fn check_progress(rpc: bool, prompt: &str, deltas: &[&str], answer: &str) {
    let server = Server::new(&[("p", &["worker"])], true).await;
    let body = request("progress-final", prompt);
    let response = if rpc {
        server.post("p", "worker", "").json(&json!({"jsonrpc":"2.0","id":1,"method":"SendStreamingMessage","params":body}))
    } else { server.post("p", "worker", "/message:stream").json(&body) }.send().await.unwrap();
    let mut live = Events::new(response, rpc);
    let Some(StreamResponse::Task(initial)) = live.next().await else { panic!("Task expected") };
    assert_eq!(initial.status.state, TaskState::Working);
    assert!(initial.artifacts.is_none());
    assert!(initial.status.message.is_none());
    let id = initial.id;
    // Gate the engine, not a sleep: every progress chunk is observed before final.
    server.mock.progress_start.cancel();
    let mut last_message_id = String::new();
    for expected in deltas {
        let Some(StreamResponse::StatusUpdate(update)) = live.next().await else { panic!("live progress expected") };
        assert_eq!(update.task_id, id);
        assert_eq!(update.status.state, TaskState::Working);
        let message = update.status.message.unwrap();
        assert_eq!(message.role, a2a::Role::Agent);
        assert_eq!(message.task_id.as_deref(), Some(id.as_str()));
        assert_eq!(message.context_id.as_deref(), Some(initial.context_id.as_str()));
        assert_eq!(message.parts[0].as_text(), Some(*expected));
        assert_ne!(message.message_id, last_message_id);
        last_message_id = message.message_id;
    }
    let current = server.task("p", "worker", &id).await;
    assert_eq!(current.status.state, TaskState::Working);
    assert!(current.artifacts.is_none(), "GetTask must not expose progress as an answer");
    assert_eq!(current.status.message.as_ref().unwrap().message_id, last_message_id);
    let listed: a2a::ListTasksResponse = server.get("p", "worker", "/tasks").query(&[("includeArtifacts", "true")])
        .send().await.unwrap().error_for_status().unwrap().json().await.unwrap();
    assert_eq!(listed.tasks.len(), 1); assert_eq!(listed.tasks[0].id, id);
    assert!(listed.tasks[0].artifacts.is_none(), "ListTasks must not expose progress as an answer");
    let response = if rpc {
        server.post("p", "worker", "").json(&json!({"jsonrpc":"2.0","id":2,"method":"SubscribeToTask","params":{"id":id}}))
    } else { server.post("p", "worker", &format!("/tasks/{id}:subscribe")) }.send().await.unwrap();
    let mut resumed = Events::new(response, rpc);
    let Some(StreamResponse::Task(snapshot)) = resumed.next().await else { panic!("recovery Task expected") };
    assert!(snapshot.artifacts.is_none());
    assert_eq!(snapshot.status.message.unwrap().message_id, last_message_id);
    server.mock.release.cancel();
    for stream in [&mut live, &mut resumed] {
        let Some(StreamResponse::ArtifactUpdate(update)) = stream.next().await else { panic!("authoritative answer expected") };
        assert_eq!(update.artifact.artifact_id, "answer");
        assert_eq!(update.artifact.parts[0].as_text(), Some(answer));
        assert_eq!(update.append, Some(false)); assert_eq!(update.last_chunk, Some(true));
        let Some(StreamResponse::StatusUpdate(update)) = stream.next().await else { panic!("terminal expected") };
        assert_eq!(update.status.state, TaskState::Completed);
        assert!(update.status.message.is_none());
        assert!(stream.next().await.is_none());
    }
    let final_task = server.task("p", "worker", &id).await;
    assert_eq!(final_task.artifacts.unwrap()[0].parts[0].as_text(), Some(answer));
    assert!(final_task.status.message.is_none());
    assert!(server.states[0].runs.get(&id).is_none());
    server.close().await;
}

#[tokio::test]
async fn rest_and_jsonrpc_stream_progress_separately_from_codex_and_claude_answers() {
    // These are the text_delta and result values in the existing Claude fixture.
    let claude: Vec<Value> = include_str!("../fixtures/cc_turn.ndjson").lines().map(|line| serde_json::from_str(line).unwrap()).collect();
    let claude_deltas: Vec<_> = claude.iter().filter_map(|event| event.pointer("/event/delta/text").and_then(Value::as_str)).collect();
    let claude_answer = claude.iter().find(|event| event["type"] == "result").unwrap()["result"].as_str().unwrap();
    for rpc in [false, true] {
        for (prompt, deltas, answer) in [
            ("codex-progress", vec!["Checking the PR..."], "Found two issues."),
            ("claude-progress", claude_deltas.clone(), claude_answer),
            ("empty-progress", vec!["Working..."], ""),
        ] {
            tokio::time::timeout(Duration::from_secs(10), check_progress(rpc, prompt, &deltas, answer)).await.unwrap();
        }
    }
}
