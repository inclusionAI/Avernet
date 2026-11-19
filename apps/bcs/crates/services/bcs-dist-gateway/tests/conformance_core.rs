//! The core rules over the real store providers and the in-memory store model.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]

#[path = "../../bcs-dist-gateway-store/tests/common/mod.rs"]
mod common;

use bcs_dist_gateway::DistGatewayCore;
use bcs_dist_model::*;
use bcs_service_api::core::dist_gateway::*;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_service_api::port::repo::distributed::inbox::*;
use bcs_test_support::contract::distributed::services::dist_gateway_core_service_contract_tests;
use common::*;
use futures::StreamExt;
use std::num::NonZeroU32;
use std::sync::Arc;

fn core(p: &Ports) -> DistGatewayCore {
    DistGatewayCore::new(p.inbox.clone(), p.tasks.clone(), p.sessions.clone(), p.blobs.clone())
}

fn new_session(input_id: &str, text: &str) -> AdmitTaskCommand {
    AdmitTaskCommand {
        input: envelope(TID, input_id, user_body(text)),
        create_session: Some(session_spec(SID, scope())),
        create_task: Some(snapshot(TID, TaskStatus::Submitted, None, NOW)),
    }
}

fn reply(input_id: &str, interaction: &str) -> AdmitTaskCommand {
    AdmitTaskCommand {
        input: envelope(
            TID,
            input_id,
            InputBody::HumanReply {
                interaction: InteractionId(interaction.into()),
                payload: Payload::Inline(serde_json::json!({ "approve": true })),
            },
        ),
        create_session: None,
        create_task: None,
    }
}

fn cancel(input_id: &str) -> AdmitTaskCommand {
    AdmitTaskCommand {
        input: envelope(TID, input_id, InputBody::Cancel { reason: None }),
        create_session: None,
        create_task: None,
    }
}

async fn waiting_for_input(p: &Ports) {
    let mut task = snapshot(TID, TaskStatus::InputRequired, Some(eid(7, 1)), NOW);
    task.content.interactions = vec![question("i-3")];
    p.worker.publish(&task).await;
}

#[tokio::test]
async fn the_core_contract_harness_passes() {
    let p = ports();
    dist_gateway_core_service_contract_tests(&core(&p), &scope(), new_session("in-1", "hello"))
        .await
        .unwrap();
}

#[tokio::test]
async fn same_input_is_one_success_and_changed_content_conflicts() {
    let p = ports();
    let c = core(&p);
    let CoreAdmission(first) = c.admit(&scope(), new_session("in-1", "hello")).await.unwrap();
    let CoreAdmission(again) = c.admit(&scope(), new_session("in-1", "hello")).await.unwrap();
    assert_eq!(first, again);
    assert_eq!(
        c.admit(&scope(), new_session("in-1", "bye")).await,
        Err(DistGatewayCoreError::ContentConflict)
    );
}

#[tokio::test]
async fn a_new_session_must_carry_the_callers_scope() {
    let p = ports();
    assert_eq!(
        core(&p).admit(&other_scope(), new_session("in-1", "hello")).await,
        Err(DistGatewayCoreError::ScopeMismatch)
    );
    let mut only_session = new_session("in-1", "hello");
    only_session.create_task = None;
    assert_eq!(
        core(&p).admit(&scope(), only_session).await,
        Err(DistGatewayCoreError::InvalidTransition)
    );
}

#[tokio::test]
async fn a_new_task_needs_an_existing_session_of_the_caller() {
    let p = ports();
    let c = core(&p);
    let second_task = |scope: TaskScope| {
        let mut task = snapshot(OTHER_TID, TaskStatus::Submitted, None, NOW);
        task.scope = scope;
        AdmitTaskCommand {
            input: envelope(OTHER_TID, "in-2", user_body("next")),
            create_session: None,
            create_task: Some(task),
        }
    };
    assert_eq!(c.admit(&scope(), second_task(scope())).await, Err(DistGatewayCoreError::NotFound));
    c.admit(&scope(), new_session("in-1", "hello")).await.unwrap();
    assert_eq!(
        c.admit(&other_scope(), second_task(other_scope())).await,
        Err(DistGatewayCoreError::ScopeMismatch)
    );
    assert_eq!(
        c.admit(&other_scope(), second_task(scope())).await,
        Err(DistGatewayCoreError::ScopeMismatch)
    );
    assert!(c.admit(&scope(), second_task(scope())).await.is_ok());
}

#[tokio::test]
async fn a_reply_needs_an_open_interaction_on_a_waiting_task() {
    let p = ports();
    let c = core(&p);
    assert_eq!(c.admit(&scope(), reply("in-r", "i-3")).await, Err(DistGatewayCoreError::NotFound));
    c.admit(&scope(), new_session("in-1", "hello")).await.unwrap();
    // Not waiting for input yet.
    assert_eq!(
        c.admit(&scope(), reply("in-r", "i-3")).await,
        Err(DistGatewayCoreError::InvalidTransition)
    );
    waiting_for_input(&p).await;
    assert_eq!(
        c.admit(&scope(), reply("in-r", "i-other")).await,
        Err(DistGatewayCoreError::InvalidTransition)
    );
    assert_eq!(c.admit(&other_scope(), reply("in-r", "i-3")).await, Err(DistGatewayCoreError::NotFound));
    assert!(c.admit(&scope(), reply("in-r", "i-3")).await.is_ok());
    // A plain user message cannot target an existing task.
    let follow_up = AdmitTaskCommand {
        input: envelope(TID, "in-u", user_body("more")),
        create_session: None,
        create_task: None,
    };
    assert_eq!(c.admit(&scope(), follow_up).await, Err(DistGatewayCoreError::InvalidTransition));
}

#[tokio::test]
async fn a_finished_task_takes_no_reply_and_no_cancel() {
    let p = ports();
    let c = core(&p);
    c.admit(&scope(), new_session("in-1", "hello")).await.unwrap();
    assert!(c.admit(&scope(), cancel("in-c1")).await.is_ok(), "a running task can be canceled");
    p.worker.publish(&snapshot(TID, TaskStatus::Completed, Some(eid(9, 1)), NOW)).await;
    assert_eq!(c.admit(&scope(), cancel("in-c2")).await, Err(DistGatewayCoreError::InvalidTransition));
    assert_eq!(c.admit(&scope(), reply("in-r", "i-3")).await, Err(DistGatewayCoreError::InvalidTransition));
    assert_eq!(c.admit(&other_scope(), cancel("in-c3")).await, Err(DistGatewayCoreError::NotFound));
}

struct FailingInbox(StoreError);
#[async_trait::async_trait]
impl InboxProducerPort for FailingInbox {
    async fn admit(&self, _request: AdmitRequest) -> Result<AdmitOutcome, StoreError> {
        Err(self.0.clone())
    }
    async fn append_stream(&self, _request: AppendAgentEvent) -> Result<InboxPos, StoreError> {
        Err(self.0.clone())
    }
}

struct QuarantinedInbox;
#[async_trait::async_trait]
impl InboxProducerPort for QuarantinedInbox {
    async fn admit(&self, _request: AdmitRequest) -> Result<AdmitOutcome, StoreError> {
        Ok(AdmitOutcome::Quarantined)
    }
    async fn append_stream(&self, _request: AppendAgentEvent) -> Result<InboxPos, StoreError> {
        unreachable!("not used by the Gateway")
    }
}

#[tokio::test]
async fn provider_outcomes_map_to_core_errors() {
    let p = ports();
    let with = |inbox: Arc<dyn InboxProducerPort>| {
        DistGatewayCore::new(inbox, p.tasks.clone(), p.sessions.clone(), p.blobs.clone())
    };
    let unknown = with(Arc::new(FailingInbox(StoreError::OutcomeUnknown {
        operation_id: "in-1".into(),
    })));
    assert_eq!(
        unknown.admit(&scope(), new_session("in-1", "hello")).await,
        Err(DistGatewayCoreError::OutcomeUnknown {
            input_id: InputId("in-1".into())
        })
    );
    let down = with(Arc::new(FailingInbox(StoreError::NotApplied {
        code: "store_not_applied".into(),
    })));
    assert_eq!(
        down.admit(&scope(), new_session("in-1", "hello")).await,
        Err(DistGatewayCoreError::Unavailable {
            code: "store_not_applied".into()
        })
    );
    assert_eq!(
        with(Arc::new(QuarantinedInbox)).admit(&scope(), new_session("in-1", "hello")).await,
        Err(DistGatewayCoreError::Quarantined)
    );
}

#[tokio::test]
async fn restore_inlines_a_referenced_snapshot() {
    let p = ports();
    let c = core(&p);
    assert_eq!(c.restore_task(&scope(), &Tid(TID.into())).await, Err(DistGatewayCoreError::NotFound));
    let mut task = snapshot(TID, TaskStatus::Working, Some(eid(2, 1)), NOW);
    task.content.snapshot = Payload::Ref(p.blobs.put("snap/b21e", br#"{"artifacts":["big"]}"#));
    p.worker.create(&task).await;
    let cut = c.restore_task(&scope(), &Tid(TID.into())).await.unwrap();
    assert_eq!(cut.view.content.snapshot, Payload::Inline(serde_json::json!({ "artifacts": ["big"] })));
    assert_eq!(cut.cursor.unwrap().token.value, "2-1");

    task.content.snapshot = Payload::Ref(BlobRef {
        key: "snap/missing".into(),
        digest: Digest("sha256:x".into()),
    });
    p.worker.publish(&task).await;
    assert_eq!(
        c.restore_task(&scope(), &Tid(TID.into())).await,
        Err(DistGatewayCoreError::Unavailable {
            code: "content_blob_missing".into()
        })
    );
}

#[tokio::test]
async fn follow_inlines_referenced_artifact_content() {
    let p = ports();
    let c = core(&p);
    p.worker.create(&snapshot(TID, TaskStatus::Working, None, NOW)).await;
    let cut = c.restore_task(&scope(), &Tid(TID.into())).await.unwrap();
    let mut follow = c.follow_task(scope(), cut.cursor.unwrap());
    let mut event = text_event(TID, eid(1, 1), "unused");
    if let OutputBody::Artifact { content, .. } = &mut event.body {
        *content = Payload::Ref(p.blobs.put("res/b21e/n1", br#"{"text":"large"}"#));
    }
    p.worker.append(&event).await;
    let Some(Ok(CoreFollowItem::Events { events, next })) = follow.next().await else {
        panic!("expected an event");
    };
    assert_eq!(next.token.value, "1-1");
    let OutputBody::Artifact { content, .. } = &events[0].body else {
        panic!("expected an artifact");
    };
    assert_eq!(content, &Payload::Inline(serde_json::json!({ "text": "large" })));
}

#[tokio::test]
async fn list_checks_the_session_before_listing() {
    let p = ports();
    let c = core(&p);
    let query = || CoreTaskQuery {
        sid: Some(Sid(SID.into())),
        statuses: vec![],
        after: None,
        limit: NonZeroU32::new(10).unwrap(),
    };
    assert_eq!(c.list_tasks(&scope(), query()).await, Err(DistGatewayCoreError::NotFound));
    c.admit(&scope(), new_session("in-1", "hello")).await.unwrap();
    assert_eq!(c.list_tasks(&other_scope(), query()).await, Err(DistGatewayCoreError::ScopeMismatch));
    let page = c.list_tasks(&scope(), query()).await.unwrap();
    assert_eq!(page.tasks.len(), 1);
    assert_eq!(page.tasks[0].tid.0, TID);
}
