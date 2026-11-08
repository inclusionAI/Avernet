//! Operation output lifecycle regressions: staged release, cancellation and duplicate admission.

#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Arc;

use bcs_message_flow::BcsMessageFlow;
use bcs_service_api::{MessageFlowService, ServiceError, ServiceResult};

#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;

#[path = "support/operation_output.rs"]
mod operation_output;
use operation_output::*;

#[path = "support/paused_delivery.rs"]
mod paused_delivery;

// ---------------------------------------------------------------------------
// Recheck R8/R9 regressions: staged releases vs already-running
// siblings, and the commit/cancel boundary of the managed lane.
// ---------------------------------------------------------------------------

/// R8: the second run's register_send_context failure must NOT
/// release the FIRST run — it already registered and delivered,
/// and its admitted output stays bound until its own terminal.
#[tokio::test]
async fn a_later_registration_failure_never_releases_the_run_already_sent() {
    /// Fails only the SECOND register_active_run.
    struct FailSecondContext {
        inner: bcs_message_flow::MemoryBotRunContextStore,
        calls: std::sync::atomic::AtomicUsize,
    }

    #[async_trait::async_trait]
    impl bcs_service_api::BotRunContextPort for FailSecondContext {
        async fn put_context(&self, context: bcs_service_api::BotRunContext) {
            self.inner.put_context(context).await
        }
        async fn get_context(&self, run_id: &str) -> Option<bcs_service_api::BotRunContext> {
            self.inner.get_context(run_id).await
        }
        async fn try_begin_terminal(&self, run_id: &str) -> bool {
            self.inner.try_begin_terminal(run_id).await
        }
        async fn mark_terminal(&self, run_id: &str) -> bool {
            self.inner.mark_terminal(run_id).await
        }
        async fn release_terminal(&self, run_id: &str) {
            self.inner.release_terminal(run_id).await
        }
        async fn begin_provider_transport(&self, run_id: &str, deadline_ms: u64) -> bool {
            self.inner
                .begin_provider_transport(run_id, deadline_ms)
                .await
        }
        async fn bind_provider_transport(
            &self,
            run_id: &str,
            transport: bcs_service_api::ProviderRunTransport,
        ) -> bool {
            self.inner
                .bind_provider_transport(run_id, transport)
                .await
        }
        async fn get_provider_transport(
            &self,
            run_id: &str,
        ) -> Option<bcs_service_api::ProviderRunTransport> {
            self.inner.get_provider_transport(run_id).await
        }
        async fn mark_provider_transport_terminal(&self, run_id: &str) {
            self.inner.mark_provider_transport_terminal(run_id).await
        }
        async fn clear_provider_transport(&self, run_id: &str) {
            self.inner.clear_provider_transport(run_id).await
        }
        async fn register_active_run(
            &self,
            context: bcs_service_api::ActiveBotRunContext,
        ) -> ServiceResult<()> {
            if self.calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst) == 1 {
                return Err(ServiceError::InternalError(
                    "injected second run registration failure".into(),
                ));
            }
            self.inner.register_active_run(context).await
        }
    }

    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let admission = Arc::new(RecordingOutputAdmission::default());
    let contexts = Arc::new(FailSecondContext {
        inner: bcs_message_flow::MemoryBotRunContextStore::new(),
        calls: Default::default(),
    });
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_run_context(contexts)
    .with_output_admission(Some(admission.clone()));

    let result = flow
        .handle_web_send(web_send(
            "group-1",
            "human_1",
            None,
            &["bot-driver", "bot-observer"],
        ))
        .await;
    assert!(result.is_err(), "the send aborts on the second registration");
    let frames = support.bot_delivery.frames().await;
    assert_eq!(
        frames.len(),
        1,
        "the first bot was already delivered before the failure"
    );
    let first_run = match &frames[0] {
        bcs_protocol::BcsFrame::Request(request) => request.id.clone(),
        other => panic!("expected a Bot request frame, got {other:?}"),
    };
    let released = admission.released_runs.lock().unwrap().clone();
    assert!(
        !released.iter().any(|(_, runs)| runs.contains(&first_run)),
        "the already-delivered run must never be released for a sibling failure: {released:?}"
    );
    assert!(
        !released.is_empty(),
        "the run whose registration failed IS released: {released:?}"
    );

    // And the surviving first run keeps its admitted operation tag.
    flow.handle_bot_event(delta_event(&first_run, "bot-driver", None))
        .await
        .expect("first run still publishes");
    let events = support.frontend_delivery.commands().await;
    assert!(
        events
            .last()
            .is_some_and(|event| event.operation_id.is_some()),
        "the surviving run's callback keeps its operation identity"
    );
}

/// R9: a managed commit that succeeds while the caller is then
/// CANCELLED still leaves the operation fully registered — the
// permit store never sits prepared (sweepable) behind durable work.
#[tokio::test]
async fn cancel_after_managed_commit_still_leaves_the_operation_registered() {
    use bcs_service_api::ManagedMessageDeliveryService;

    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let managed = Arc::new(bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(
        repo.clone(),
    ));
    let paused = Arc::new(paused_delivery::PausedDelivery::new(managed.clone()));
    let admission = Arc::new(RecordingOutputAdmission::default());
    let flow = managed_output_flow(&support, repo, paused.clone(), admission.clone(), 10)
        .await;
    let flow = Arc::new(flow);

    let sender = flow.clone();
    let task = tokio::spawn(async move {
        sender
            .handle_web_send(web_send(
                "group-1",
                "human_1",
                Some("group-1:ffffffff"),
                &["bot-driver"],
            ))
            .await
    });
    tokio::time::timeout(std::time::Duration::from_secs(5), paused.committed.notified())
        .await
        .expect("admission committed");
    assert!(
        !managed.snapshot(None).await.expect("snapshot").is_empty(),
        "the durable accepted delivery persists"
    );
    // The caller is CANCELLED exactly at the durable commit boundary.
    task.abort();
    let _ = task.await;

    let registered = admission.registered_runs.lock().unwrap().clone();
    assert!(
        !registered.is_empty(),
        "a durably accepted queued Run must already be registered — \
         never a prepared-only permit behind the prepare timeout"
    );
    assert!(
        registered[0].0.len() >= 8,
        "the registration carries the admitted operation identity: {registered:?}"
    );
}

/// A duplicate (idempotent) send never re-uses its fresh run
/// identities: the whole freshly-registered set is released exactly
/// — the original operation is untouched.
#[tokio::test]
async fn duplicate_managed_send_releases_only_its_fresh_registrations() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let managed = Arc::new(bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(
        repo.clone(),
    ));
    let admission = Arc::new(RecordingOutputAdmission::default());
    let flow = managed_output_flow(&support, repo, managed.clone(), admission.clone(), 10)
        .await;

    let mut first_cmd = web_send("group-1", "human_1", Some("group-1:ffffffff"), &["bot-driver"]);
    first_cmd.idempotency_key = Some("dup-1".to_string());
    let first = flow
        .handle_web_send(first_cmd.clone())
        .await
        .expect("first send admitted");
    let second = flow
        .handle_web_send(first_cmd)
        .await
        .expect("second send returns the duplicate outcome");
    assert!(
        second.queue_admission.is_some(),
        "the second send is the idempotent duplicate"
    );
    assert_ne!(
        first.operation_id, second.operation_id,
        "each admission gets its own prepared operation identity"
    );

    let registered = admission.registered_runs.lock().unwrap().clone();
    assert_eq!(registered.len(), 2, "both operations registered once");
    let released = admission.released_runs.lock().unwrap().clone();
    assert_eq!(released.len(), 1, "exactly the duplicate was released");
    assert_eq!(released[0].0, second.operation_id);
    assert_eq!(
        released[0].1, registered[1].1,
        "the released set is precisely the duplicate's fresh registrations"
    );
}
