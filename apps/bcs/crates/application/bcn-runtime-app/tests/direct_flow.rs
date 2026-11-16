mod common;
use bcn_runtime_api::{application::*, port::repo::*};
use bcn_runtime_model::*;
use common::*;
use std::{sync::Arc, sync::atomic::Ordering};
#[tokio::test]
async fn accepted_before_activation_recovers_and_saves_result_before_terminal() {
    let f = Fixture::new();
    let g = f.gateway();
    let task = g.submit(caller(), request("one")).await.unwrap();
    assert!(f.repo.load(&task.session_id).await.unwrap().is_none());
    let w = f.worker("worker");
    let t = Arc::new(Transport::new(vec![
        Ok(AgentEvent::Accepted { handle: handle() }),
        Ok(AgentEvent::Delta {
            text: "live".into(),
        }),
        Ok(AgentEvent::Completed {
            output: text("final"),
        }),
    ]));
    let c = f.connector("connector", t.clone());
    f.results.down.store(true, Ordering::SeqCst);
    f.pump(&w, &c, 40).await;
    let pending = g.get(caller(), task.task_id.clone()).await.unwrap();
    assert_eq!(pending.status, TaskStatus::PendingResult);
    assert_ne!(pending.output, text("final"));
    f.results.down.store(false, Ordering::SeqCst);
    f.pump(&w, &c, 40).await;
    let done = g.submit(caller(), request("one")).await.unwrap();
    assert_eq!(done.status, TaskStatus::Completed);
    assert_eq!(done.output, text("final"));
    assert_eq!(t.sends.load(Ordering::SeqCst), 1);
}
#[tokio::test]
async fn uncertain_stream_without_handle_preserves_partial_output_and_never_resends() {
    let f = Fixture::new();
    let g = f.gateway();
    let task = g.submit(caller(), request("unknown")).await.unwrap();
    let w = f.worker("worker");
    let t = Arc::new(Transport::new(vec![
        Ok(AgentEvent::Delta {
            text: "partial".into(),
        }),
        Err(ConnectorError::MayHaveBeenAccepted("broken".into())),
    ]));
    let c = f.connector("connector", t.clone());
    f.pump(&w, &c, 80).await;
    let done = g.get(caller(), task.task_id).await.unwrap();
    assert_eq!(done.status, TaskStatus::Failed);
    assert_eq!(done.error, Some(RuntimeError::ExecutionUnknown));
    assert_eq!(done.output, text("partial"));
    assert_eq!(t.sends.load(Ordering::SeqCst), 1);
}
#[tokio::test]
async fn direct_message_response_preserves_server_context_for_subsequent_task() {
    use async_trait::async_trait;
    use bcn_runtime_api::port::{AgentCatalog, AgentConnector};
    use bcn_runtime_app::DefaultGateway;
    struct Messages(std::sync::Mutex<Vec<AgentSendRequest>>);
    #[async_trait]
    impl AgentConnector for Messages {
        fn capabilities(&self) -> ConnectorCapabilities {
            let mut c = binding().capabilities;
            c.streaming = false;
            c
        }
        async fn send_message(&self, r: AgentSendRequest) -> ConnectorResult<AgentSendResponse> {
            self.0.lock().unwrap().push(r);
            let mut h = handle();
            h.remote_task_id = None;
            h.remote_context_id = Some("server-selected-context".into());
            Ok(AgentSendResponse::Message { observation_error: None,
                parts: text("message-result"),
                handle: Some(h),
            })
        }
    }
    struct SyncCatalog;
    #[async_trait]
    impl AgentCatalog for SyncCatalog {
        async fn resolve(
            &self,
            c: &CallerContext,
            t: &ExecutionTarget,
            s: &SessionId,
        ) -> RuntimeResult<SessionSpec> {
            let mut spec = Catalog.resolve(c, t, s).await?;
            spec.bindings
                .values_mut()
                .next()
                .unwrap()
                .capabilities
                .streaming = false;
            Ok(spec)
        }
    }
    let f = Fixture::new();
    let g = DefaultGateway::new(
        f.ports.clone(),
        f.runners(),
        Arc::new(SyncCatalog),
        vec![Arc::new(Allow)],
        f.clock.clone(),
        true,
    )
    .unwrap();
    let w = f.worker("w");
    let transport = Arc::new(Messages(std::sync::Mutex::new(vec![])));
    let mut binding = binding();
    binding.capabilities.streaming = false;
    let c = bcn_runtime_app::DefaultConnector::new(
        node("c"),
        f.ports.clone(),
        vec![(binding, transport.clone())],
        f.clock.clone(),
    )
    .unwrap();
    let first = g.submit(caller(), request("sync1")).await.unwrap();
    f.pump(&w, &c, 10).await;
    assert_eq!(
        g.get(caller(), first.task_id).await.unwrap().status,
        TaskStatus::Completed
    );
    let mut next = request("sync2");
    next.message.context_id = Some(first.session_id);
    g.submit(caller(), next).await.unwrap();
    f.pump(&w, &c, 10).await;
    let calls = transport.0.lock().unwrap();
    assert_eq!(calls.len(), 2);
    let h = calls[1].handle.as_ref().unwrap();
    assert_eq!(
        h.remote_context_id.as_deref(),
        Some("server-selected-context")
    );
    assert!(h.remote_task_id.is_none());
}
