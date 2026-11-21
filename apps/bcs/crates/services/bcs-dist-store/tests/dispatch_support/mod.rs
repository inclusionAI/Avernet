#![allow(
    dead_code,
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "provider fixtures"
)]
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_dist_store::dispatch::{DispatchConfig, DispatchStores, StoreDispatch};
use bcs_service_api::port::repo::distributed::{common::*, dispatch::*, ownership::*, session::*};
use std::{
    sync::{Arc, Mutex},
    time::Duration,
};
pub struct Members(pub Mutex<Liveness>);
#[async_trait]
impl MembershipPort for Members {
    async fn register(
        &self,
        instance: Instance,
        _: RuntimeRole,
        _: String,
        _: Duration,
    ) -> Result<MembershipLease, StoreError> {
        Ok(MembershipLease { instance })
    }
    async fn renew(&self, _: &MembershipLease, _: Duration) -> Result<Renewal, StoreError> {
        Ok(Renewal::Renewed)
    }
    async fn set_draining(&self, _: &MembershipLease) -> Result<Renewal, StoreError> {
        Ok(Renewal::Renewed)
    }
    async fn retire(&self, _: &MembershipLease) -> Result<(), StoreError> {
        Ok(())
    }
    async fn liveness(&self, instance: &Instance) -> Result<Liveness, StoreError> {
        Ok(if instance.node == "other" {
            Liveness::Alive
        } else {
            *self.0.lock().unwrap()
        })
    }
    async fn members(
        &self,
        _: RuntimeRole,
        _: PageRequest,
    ) -> Result<Page<MemberRecord>, StoreError> {
        Ok(Page {
            items: vec![],
            next: None,
        })
    }
}
pub struct Fixture {
    pub base: crate::task_feed_support::Fixture,
    pub membership: Arc<Members>,
    pub board: StoreDispatch,
}
impl Fixture {
    pub async fn new() -> Self {
        let base = crate::task_feed_support::Fixture::new().await;
        let membership = Arc::new(Members(Mutex::new(Liveness::Alive)));
        let board = build(&base, membership.clone(), DispatchConfig::default());
        Self {
            base,
            membership,
            board,
        }
    }
    pub fn reopen(&self) -> StoreDispatch {
        build(
            &self.base,
            self.membership.clone(),
            DispatchConfig::default(),
        )
    }
    pub async fn create(&self) -> CreateCommittedDispatch {
        let spec = spec();
        let sources = self
            .base
            .commit(1, vec![EffectKind::CreateDispatch { spec: spec.clone() }])
            .await;
        let request = CreateCommittedDispatch {
            source: sources[0].clone(),
            spec,
        };
        assert_eq!(
            self.board.create(request.clone()).await.unwrap(),
            IdempotentWrite::Applied
        );
        request
    }
    pub async fn input(&self, sequence: u64, seq: u64) -> CommittedDispatchInput {
        let input = PendingInput {
            seq: InputSeq(seq),
            input_id: InputId(format!("input-{seq}")),
            business_digest: Digest(format!("digest-{seq}")),
            payload: Payload::Inline(serde_json::json!({"text":seq})),
        };
        let did = spec().did;
        let sources = self
            .base
            .commit(
                sequence,
                vec![EffectKind::PushInput {
                    did: did.clone(),
                    input: input.clone(),
                }],
            )
            .await;
        CommittedDispatchInput {
            source: sources[0].clone(),
            did,
            input,
        }
    }
    pub async fn cancel(&self, sequence: u64) -> CommittedCancelIntent {
        let did = spec().did;
        let intent = CancelIntent {
            request_id: RequestId("cancel".into()),
            reason: Some("user".into()),
        };
        let sources = self
            .base
            .commit(
                sequence,
                vec![EffectKind::RequestCancel {
                    did: did.clone(),
                    intent: intent.clone(),
                }],
            )
            .await;
        CommittedCancelIntent {
            source: sources[0].clone(),
            did,
            intent,
        }
    }
    pub async fn claim(&self) -> Box<dyn HeldDispatchPort> {
        match self
            .board
            .claim(DispatchClaimRequest {
                did: spec().did,
                me: me(),
                request_id: RequestId("claim".into()),
            })
            .await
            .unwrap()
        {
            ClaimOutcome::Acquired(h) => h,
            _ => panic!("acquired"),
        }
    }
}
pub fn build(
    base: &crate::task_feed_support::Fixture,
    membership: Arc<Members>,
    config: DispatchConfig,
) -> StoreDispatch {
    StoreDispatch::new(
        DispatchStores {
            scan_budget: Arc::new(
                bcs_dist_store::budget::SharedScanBudget::new(
                    bcs_dist_store::budget::ScanBudgetConfig::default(),
                )
                .unwrap(),
            ),
            documents: base.storage.clone(),
            streams: base.storage.clone(),
            index: base.storage.clone(),
            membership,
        },
        base.layout.clone(),
        config,
    )
    .unwrap()
}
pub fn me() -> Instance {
    Instance {
        node: "connector".into(),
        incarnation: "one".into(),
    }
}
pub fn spec() -> DispatchSpec {
    DispatchSpec {
        schema_version: 1,
        sid: Sid(crate::common::SID.into()),
        tid: Tid(crate::common::TID.into()),
        did: Did(format!("{}.d1", crate::common::TID)),
        node: NodeId("node".into()),
        attempt: 1,
        target: AgentBinding {
            agent: AgentId("agent".into()),
            binding_version: BindingVersion("v1".into()),
            credential_ref: CredentialRef("ref".into()),
        },
        pool: PoolId("a2a".into()),
        capabilities: DownstreamCapabilities {
            idempotent_start: true,
            idempotent_input: true,
            query_by_request: true,
            query_by_task: true,
            subscribe: true,
            resume_events: true,
            cancel: true,
        },
        request: Payload::Inline(serde_json::json!({"prompt":"hello"})),
        deadline: TimestampMillis(60000),
    }
}
pub fn start() -> SendOperation {
    SendOperation::Start {
        request_id: RequestId("start".into()),
    }
}
pub fn acceptance(operation: SendOperation) -> AcceptanceEvidence {
    AcceptanceEvidence {
        operation,
        remote: RemoteTaskRef {
            task_id: "remote".into(),
            context_id: None,
        },
    }
}
pub fn page() -> PageRequest {
    PageRequest {
        after: None,
        limit: std::num::NonZeroU32::new(128).unwrap(),
    }
}

pub struct SlowDocuments {
    pub inner: Arc<crate::common::Recorder>,
    pub delay: Duration,
}
impl bcs_store_api::StorePlugin for SlowDocuments {
    fn pool_observation(&self) -> Option<bcs_store_api::StorePoolObservation> {
        self.inner.pool_observation()
    }
    fn supports(&self, c: bcs_store_api::StoreCapability) -> bool {
        self.inner.supports(c)
    }
}
#[async_trait]
impl bcs_store_api::document::DocumentStorePlugin for SlowDocuments {
    async fn read_document(
        &self,
        key: &str,
    ) -> bcs_store_api::StoreResult<Option<bcs_store_api::Versioned<serde_json::Value>>> {
        tokio::time::sleep(self.delay).await;
        self.inner.read_document(key).await
    }
    async fn create_document(
        &self,
        key: &str,
        value: &serde_json::Value,
    ) -> bcs_store_api::StoreResult<bcs_store_api::WriteOutcome> {
        tokio::time::sleep(self.delay).await;
        self.inner.create_document(key, value).await
    }
    async fn replace_document(
        &self,
        key: &str,
        version: bcs_store_api::Version,
        value: &serde_json::Value,
    ) -> bcs_store_api::StoreResult<bcs_store_api::WriteOutcome> {
        tokio::time::sleep(self.delay).await;
        self.inner.replace_document(key, version, value).await
    }
}

pub struct HangingDocument {
    pub inner: Arc<crate::common::Recorder>,
    pub key: String,
}
impl bcs_store_api::StorePlugin for HangingDocument {
    fn pool_observation(&self) -> Option<bcs_store_api::StorePoolObservation> {
        self.inner.pool_observation()
    }
    fn supports(&self, c: bcs_store_api::StoreCapability) -> bool {
        self.inner.supports(c)
    }
}
#[async_trait]
impl bcs_store_api::document::DocumentStorePlugin for HangingDocument {
    async fn read_document(
        &self,
        key: &str,
    ) -> bcs_store_api::StoreResult<Option<bcs_store_api::Versioned<serde_json::Value>>> {
        if key == self.key {
            tokio::time::sleep(Duration::from_secs(1)).await;
        }
        self.inner.read_document(key).await
    }
    async fn create_document(
        &self,
        key: &str,
        value: &serde_json::Value,
    ) -> bcs_store_api::StoreResult<bcs_store_api::WriteOutcome> {
        self.inner.create_document(key, value).await
    }
    async fn replace_document(
        &self,
        key: &str,
        version: bcs_store_api::Version,
        value: &serde_json::Value,
    ) -> bcs_store_api::StoreResult<bcs_store_api::WriteOutcome> {
        self.inner.replace_document(key, version, value).await
    }
}
