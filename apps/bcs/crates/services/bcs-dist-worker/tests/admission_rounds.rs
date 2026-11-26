//! Worker-side canonical strict-round admission entry: scope pinning and
//! delegation onto the group repo. The store-level CAS semantics are covered
//! by `bcs-dist-store --test conformance_group_round` on the real provider.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]

use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{
    common::StoreError,
    group_round::{
        GroupRequestIdentity, GroupRequestRecord, GroupRoundRepo, GroupRoundView, GroupStartRequest,
    },
};
use bcs_dist_worker::WorkerTaskAdmission;
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};

struct RecordingRounds {
    calls: AtomicUsize,
}

#[async_trait]
impl GroupRoundRepo for RecordingRounds {
    async fn lookup(
        &self,
        _identity: &GroupRequestIdentity<'_>,
    ) -> Result<GroupRequestRecord, StoreError> {
        Ok(GroupRequestRecord::Absent)
    }
    async fn admit(&self, _request: &GroupStartRequest) -> Result<Admission, StoreError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        Ok(Admission::Expired)
    }
    async fn get(&self, _key: &RoundKey) -> Result<Option<GroupRoundView>, StoreError> {
        Ok(None)
    }
}

fn inbox() -> Arc<dyn bcs_service_api::port::repo::distributed::inbox::InboxProducerPort> {
    // Never touched by the strict entry; a stub provider is enough here.
    Arc::new(NoInbox)
}

struct NoInbox;

#[async_trait]
impl bcs_service_api::port::repo::distributed::inbox::InboxProducerPort for NoInbox {
    async fn admit(
        &self,
        _request: bcs_service_api::port::repo::distributed::inbox::AdmitRequest,
    ) -> Result<
        bcs_service_api::port::repo::distributed::inbox::AdmitOutcome,
        StoreError,
    > {
        Err(StoreError::NotApplied {
            code: "not_wired".into(),
        })
    }
    async fn append_stream(
        &self,
        _request: bcs_service_api::port::repo::distributed::inbox::AppendAgentEvent,
    ) -> Result<InboxPos, StoreError> {
        Err(StoreError::NotApplied {
            code: "not_wired".into(),
        })
    }
}

fn scope() -> TaskScope {
    TaskScope {
        tenant: TenantId("t-ant".into()),
        principal: PrincipalId("user-001".into()),
    }
}

fn round(scope: &TaskScope) -> GroupStartRequest {
    AcceptedRound {
        input: RoundInput {
            schema_version: RoundSchemaVersion::V1,
            source: GroupRoundSource::GroupA2a,
            key: RoundKey {
                scope: scope.clone(),
                group: GroupId("grp-round".into()),
                sid: Sid("s0042.round".into()),
                tid: Tid("s0042.round.msg-a.g1".into()),
                generation: 1.try_into().unwrap(),
            },
            ordered_parts: vec![RoundPart::Text {
                text: "hello".into(),
            }],
            workflow_ref: WorkflowRef {
                definition_id: "wf".into(),
                revision: "1".into(),
                digest: Digest("wf".into()),
            },
            history_cut: BlobRef {
                key: "history".into(),
                digest: Digest("history".into()),
            },
            execution_deadline: TimestampMillis(100_000),
        },
        request_id: RequestId("msg-a".into()),
        request_digest: Digest("digest-msg-a".into()),
        normalization_ref: BlobRef {
            key: "norm".into(),
            digest: Digest("norm".into()),
        },
        return_configuration: serde_json::json!({ "mode": "text" }),
    }
}

#[tokio::test]
async fn strict_rounds_entry_delegates_to_the_configured_repo() {
    let rounds = Arc::new(RecordingRounds {
        calls: AtomicUsize::new(0),
    });
    let service = WorkerTaskAdmission::new(inbox(), Arc::new(|| TimestampMillis(1)));
    // Without the strict repo wired the entry refuses rather than pretending.
    assert!(matches!(
        service.admit_group_round(&scope(), &round(&scope())).await,
        Err(StoreError::Invalid { ref code }) if code == "strict_rounds_unconfigured"
    ));
    let service =
        WorkerTaskAdmission::with_rounds(inbox(), rounds.clone(), Arc::new(|| TimestampMillis(1)));
    let admitted = service
        .admit_group_round(&scope(), &round(&scope()))
        .await
        .unwrap();
    assert_eq!(admitted, Admission::Expired);
    assert_eq!(rounds.calls.load(Ordering::SeqCst), 1);
    // Untrusted scope: the Gateway supplies scope, the round key must match it.
    let mut foreign_request = round(&scope());
    foreign_request.input.key.scope.principal = PrincipalId("user-002".into());
    assert!(matches!(
        service
            .admit_group_round(&scope(), &foreign_request)
            .await,
        Err(StoreError::Invalid { ref code }) if code == "group_scope_untrusted"
    ));
    assert_eq!(rounds.calls.load(Ordering::SeqCst), 1);
}
