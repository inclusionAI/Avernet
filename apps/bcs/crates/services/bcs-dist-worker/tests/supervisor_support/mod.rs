#![allow(
    dead_code,
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "supervisor provider fixtures"
)]
use crate::processor_support::{self, FinishingRunner, Fixture};
use async_trait::async_trait;
use bcs_dist_encoding::digest_json;
use bcs_dist_model::*;
use bcs_dist_worker::supervisor::*;
use bcs_service_api::port::repo::distributed::{
    budget::*, common::*, dispatch::*, inbox::*, ownership::*, session::*,
};
use std::{
    num::NonZeroU32,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};
pub struct Budget {
    pub inner: Arc<bcs_dist_store::budget::SharedScanBudget>,
    pub reject: AtomicBool,
}
impl ScanBudgetPort for Budget {
    fn profile(&self) -> ScanBudgetProfile {
        self.inner.profile()
    }
    fn try_io(&self) -> Option<ScanIoPermit> {
        if self.reject.load(Ordering::SeqCst) {
            None
        } else {
            self.inner.try_io()
        }
    }
    fn claim(&self, c: ScanClass, l: u32, s: u32) -> Result<bool, StoreError> {
        if self.reject.load(Ordering::SeqCst) {
            Ok(false)
        } else {
            self.inner.claim(c, l, s)
        }
    }
    fn claim_log_page(
        &self,
        class: ScanClass,
        limit: NonZeroU32,
    ) -> Result<Option<NonZeroU32>, StoreError> {
        if self.reject.load(Ordering::SeqCst) {
            Ok(None)
        } else {
            self.inner.claim_log_page(class, limit)
        }
    }
}
pub struct Inbox {
    pub inner: Arc<bcs_dist_store::inbox::StoreInboxProducer>,
    pub lost: AtomicBool,
    pub requests: Mutex<Vec<AdmitRequest>>,
    pub pause: AtomicBool,
    pub entered: tokio::sync::Notify,
    pub release: tokio::sync::Notify,
}
#[async_trait]
impl InboxProducerPort for Inbox {
    fn scan_budget_profile(&self) -> Option<ScanBudgetProfile> {
        InboxProducerPort::scan_budget_profile(self.inner.as_ref())
    }
    async fn admit(&self, r: AdmitRequest) -> Result<AdmitOutcome, StoreError> {
        self.requests.lock().unwrap().push(r.clone());
        let result = self.inner.admit(r.clone()).await?;
        if self.pause.swap(false, Ordering::SeqCst) {
            self.entered.notify_one();
            self.release.notified().await;
        }
        if self.lost.swap(false, Ordering::SeqCst) {
            Err(StoreError::OutcomeUnknown {
                operation_id: r.input.input_id.0,
            })
        } else {
            Ok(result)
        }
    }
    async fn append_stream(&self, r: AppendAgentEvent) -> Result<InboxPos, StoreError> {
        self.inner.append_stream(r).await
    }
}
pub struct Board {
    pub inner: Arc<bcs_dist_store::dispatch::StoreDispatch>,
    pub notify_calls: Mutex<Vec<Did>>,
    pub old_revision: AtomicBool,
    pub always_old_revision: AtomicBool,
    pub reschedule_calls: Mutex<Vec<Revision>>,
    pub fake_evidence: AtomicBool,
    pub scan_calls: Mutex<Vec<ShardId>>,
    pub slow_scan: AtomicBool,
}
#[async_trait]
impl DispatchControlPort for Board {
    fn scan_budget_profile(&self) -> Option<ScanBudgetProfile> {
        self.inner.scan_budget_profile()
    }
    async fn create(&self, r: CreateCommittedDispatch) -> Result<IdempotentWrite, StoreError> {
        self.inner.create(r).await
    }
    async fn request_cancel(&self, r: CommittedCancelIntent) -> Result<ControlOutcome, StoreError> {
        self.inner.request_cancel(r).await
    }
    async fn push_input(&self, r: CommittedDispatchInput) -> Result<ControlOutcome, StoreError> {
        self.inner.push_input(r).await
    }
    async fn notify(&self, p: &PoolId, d: &Did) -> Result<(), StoreError> {
        self.notify_calls.lock().unwrap().push(d.clone());
        self.inner.notify(p, d).await
    }
    async fn get(&self, d: &Did) -> Result<Option<DispatchRecord>, StoreError> {
        let mut record = self.inner.get(d).await?;
        if self.fake_evidence.load(Ordering::SeqCst)
            && let Some(record) = record.as_mut()
        {
            record.terminal_evidence = None;
        }
        Ok(record)
    }
    async fn scan_due(
        &self,
        s: ShardId,
        n: TimestampMillis,
        p: PageRequest,
    ) -> Result<Page<Did>, StoreError> {
        self.scan_calls.lock().unwrap().push(s);
        if s == ShardId(99) && self.slow_scan.load(Ordering::SeqCst) {
            tokio::time::sleep(Duration::from_secs(3)).await;
        }
        self.inner.scan_due(s, n, p).await
    }
    async fn reschedule(
        &self,
        d: &Did,
        r: Revision,
        n: TimestampMillis,
    ) -> Result<RecheckOutcome, StoreError> {
        self.reschedule_calls.lock().unwrap().push(r.clone());
        if self.always_old_revision.load(Ordering::SeqCst)
            || self.old_revision.swap(false, Ordering::SeqCst)
        {
            return Ok(RecheckOutcome::NeedsRecheck);
        }
        self.inner.reschedule(d, r, n).await
    }
}
pub struct Members {
    pub inner: Arc<bcs_dist_store::coordination::CoordinationStore>,
    pub unknown: AtomicBool,
}
#[async_trait]
impl MembershipPort for Members {
    async fn register(
        &self,
        i: Instance,
        r: RuntimeRole,
        a: String,
        t: Duration,
    ) -> Result<MembershipLease, StoreError> {
        self.inner.register(i, r, a, t).await
    }
    async fn renew(&self, l: &MembershipLease, t: Duration) -> Result<Renewal, StoreError> {
        self.inner.renew(l, t).await
    }
    async fn set_draining(&self, l: &MembershipLease) -> Result<Renewal, StoreError> {
        self.inner.set_draining(l).await
    }
    async fn retire(&self, l: &MembershipLease) -> Result<(), StoreError> {
        self.inner.retire(l).await
    }
    async fn liveness(&self, i: &Instance) -> Result<Liveness, StoreError> {
        if self.unknown.load(Ordering::SeqCst) {
            Ok(Liveness::Unknown)
        } else {
            self.inner.liveness(i).await
        }
    }
    async fn members(
        &self,
        r: RuntimeRole,
        p: PageRequest,
    ) -> Result<Page<MemberRecord>, StoreError> {
        self.inner.members(r, p).await
    }
}
pub struct Harness {
    pub f: Fixture,
    pub inbox: Arc<Inbox>,
    pub board: Arc<Board>,
    pub members: Arc<Members>,
    pub budget: Arc<Budget>,
    pub tid: Tid,
    pub did: Did,
}
impl Harness {
    pub async fn new() -> Self {
        let f = Fixture::new(Arc::new(FinishingRunner::default())).await;
        let tid = f.admit("task").await;
        f.warm().await;
        let did = Did(format!("{}.d1", tid.0));
        let owner = f
            .coordination
            .current(ShardId(1))
            .await
            .unwrap()
            .unwrap()
            .claim;
        let AcquireOutcome::Acquired(current) = f
            .coordination
            .acquire(
                &processor_support::spec().sid,
                &owner,
                crate::support::initial(),
            )
            .await
            .unwrap()
        else {
            panic!("acquired")
        };
        let spec = DispatchSpec {
            schema_version: 1,
            sid: processor_support::spec().sid,
            tid: tid.clone(),
            did: did.clone(),
            node: NodeId("node".into()),
            attempt: 1,
            target: AgentBinding {
                agent: AgentId("agent".into()),
                binding_version: BindingVersion("v1".into()),
                credential_ref: CredentialRef("ref".into()),
            },
            pool: PoolId("a2a".into()),
            capabilities: DownstreamCapabilities {
                idempotent_start: false,
                idempotent_input: false,
                query_by_request: false,
                query_by_task: true,
                subscribe: true,
                resume_events: false,
                cancel: true,
            },
            request: Payload::Inline(serde_json::json!("request")),
            deadline: TimestampMillis(60000),
        };
        let sequence = CommitSeq(current.envelope.checkpoint.commit_seq.0 + 1);
        let kind = EffectKind::CreateDispatch { spec: spec.clone() };
        let effect = Effect {
            id: EffectId {
                sid: spec.sid.clone(),
                commit: sequence,
                index: NonZeroU32::MIN,
            },
            content_digest: digest_json(&serde_json::to_value(&kind).unwrap()),
            kind,
        };
        let mut checkpoint = current.envelope.checkpoint;
        checkpoint.commit_seq = sequence;
        checkpoint.outbox = Some(CommittedBatch {
            created_at: None,
            commit_id: CommitId("dispatch-create".into()),
            source_commit: sequence,
            content_digest: Digest(String::new()),
            effects: vec![effect.clone()],
        });
        let digest = digest_json(&serde_json::to_value(&checkpoint).unwrap());
        checkpoint.outbox.as_mut().unwrap().content_digest = digest.clone();
        let CommitOutcome::Committed(receipt) = f
            .coordination
            .commit(CommitRequest {
                purpose: CommitPurpose::Ordinary {
                    control_revision: 0,
                },
                owner: current.ownership,
                expected_commit_seq: CommitSeq(sequence.0 - 1),
                commit_id: CommitId("dispatch-create".into()),
                content_digest: digest,
                candidate: checkpoint,
            })
            .await
            .unwrap()
        else {
            panic!("commit")
        };
        f.dispatch
            .create(CreateCommittedDispatch {
                source: EffectSource {
                    commit: receipt,
                    effect_id: effect.id,
                    content_digest: effect.content_digest,
                },
                spec,
            })
            .await
            .unwrap();
        let inbox = Arc::new(Inbox {
            inner: f.inbox.clone(),
            lost: AtomicBool::new(false),
            requests: Mutex::new(vec![]),
            pause: AtomicBool::new(false),
            entered: tokio::sync::Notify::new(),
            release: tokio::sync::Notify::new(),
        });
        let board = Arc::new(Board {
            inner: f.dispatch.clone(),
            notify_calls: Mutex::new(vec![]),
            old_revision: AtomicBool::new(false),
            always_old_revision: AtomicBool::new(false),
            reschedule_calls: Mutex::new(vec![]),
            fake_evidence: AtomicBool::new(false),
            scan_calls: Mutex::new(vec![]),
            slow_scan: AtomicBool::new(false),
        });
        let members = Arc::new(Members {
            inner: f.coordination.clone(),
            unknown: AtomicBool::new(false),
        });
        let budget = Arc::new(Budget {
            inner: f.scan_budget.clone(),
            reject: AtomicBool::new(false),
        });
        Self {
            f,
            inbox,
            board,
            members,
            budget,
            tid,
            did,
        }
    }
    pub fn supervisor(&self) -> DispatchSupervisor {
        DispatchSupervisor::new(
            SupervisorPorts {
                dispatch: self.board.clone(),
                sessions: self.f.coordination.clone(),
                inbox: self.inbox.clone(),
                membership: self.members.clone(),
                scan_budget: self.budget.clone(),
                now: Arc::new(|| TimestampMillis(1000)),
            },
            SupervisorConfig {
                shards: vec![ShardId(1)],
                retry_backoff: Duration::from_millis(1),
                ..Default::default()
            },
        )
        .unwrap()
    }
    pub async fn held(&self) -> Box<dyn HeldDispatchPort> {
        let me = crate::support::instance("connector");
        self.f
            .coordination
            .register(
                me.clone(),
                RuntimeRole::Connector,
                "http://connector.example.test".into(),
                Duration::from_secs(60),
            )
            .await
            .unwrap();
        match self
            .f
            .dispatch
            .claim(DispatchClaimRequest {
                did: self.did.clone(),
                me,
                request_id: RequestId("claim".into()),
            })
            .await
            .unwrap()
        {
            ClaimOutcome::Acquired(h) => h,
            _ => panic!("claim"),
        }
    }
    pub async fn finish(&self, outcome: FinalDispatchOutcome) {
        self.held()
            .await
            .finish(outcome, "remote-terminal-7".into())
            .await
            .unwrap();
    }
    pub async fn drive(&self, s: &mut DispatchSupervisor, n: usize) {
        for _ in 0..n {
            s.tick().await;
            tokio::time::sleep(Duration::from_millis(2)).await;
        }
    }
}

impl Harness {
    pub async fn block(&self) {
        use bcs_service_api::port::repo::distributed::control::*;
        let snapshot = self
            .f
            .coordination
            .load_control(&processor_support::spec().sid)
            .await
            .unwrap()
            .unwrap();
        let attempt = AttemptIdentity {
            id: RequestId("blocked-attempt".into()),
            head: snapshot.head,
            state_digest: snapshot.checkpoint_digest.clone(),
            input_digest: Digest("input".into()),
            context_digest: Digest("context".into()),
            frozen_now: TimestampMillis(1000),
        };
        self.f
            .coordination
            .isolate(
                ControlGuard {
                    owner: snapshot.owner,
                    expected_head: snapshot.head,
                    expected_revision: snapshot.control.revision,
                },
                attempt,
                AttemptOutcome::Uninterpretable {
                    code: "unsupported_test_format".into(),
                    isolation_id: RequestId("blocked".into()),
                },
            )
            .await
            .unwrap();
    }
    pub async fn another(&self, suffix: &str) -> Did {
        let mut spec = self
            .f
            .dispatch
            .get(&self.did)
            .await
            .unwrap()
            .unwrap()
            .view
            .spec;
        spec.did = Did(format!("{}.{suffix}", self.tid.0));
        let claim = self
            .f
            .coordination
            .current(ShardId(1))
            .await
            .unwrap()
            .unwrap()
            .claim;
        let AcquireOutcome::Acquired(current) = self
            .f
            .coordination
            .acquire(&spec.sid, &claim, crate::support::initial())
            .await
            .unwrap()
        else {
            panic!("acquired")
        };
        let sequence = CommitSeq(current.envelope.checkpoint.commit_seq.0 + 1);
        let kind = EffectKind::CreateDispatch { spec: spec.clone() };
        let effect = Effect {
            id: EffectId {
                sid: spec.sid.clone(),
                commit: sequence,
                index: NonZeroU32::MIN,
            },
            content_digest: digest_json(&serde_json::to_value(&kind).unwrap()),
            kind,
        };
        let commit_id = CommitId(format!("create-{suffix}"));
        let digest = Digest(String::new());
        let mut checkpoint = current.envelope.checkpoint;
        checkpoint.commit_seq = sequence;
        checkpoint.outbox = Some(CommittedBatch {
            created_at: None,
            commit_id: commit_id.clone(),
            source_commit: sequence,
            content_digest: digest.clone(),
            effects: vec![effect.clone()],
        });
        let digest = digest_json(&serde_json::to_value(&checkpoint).unwrap());
        checkpoint.outbox.as_mut().unwrap().content_digest = digest.clone();
        let CommitOutcome::Committed(receipt) = self
            .f
            .coordination
            .commit(CommitRequest {
                purpose: CommitPurpose::Ordinary {
                    control_revision: 0,
                },
                owner: current.ownership,
                expected_commit_seq: CommitSeq(sequence.0 - 1),
                commit_id,
                content_digest: digest,
                candidate: checkpoint,
            })
            .await
            .unwrap()
        else {
            panic!("committed")
        };
        self.f
            .dispatch
            .create(CreateCommittedDispatch {
                source: EffectSource {
                    commit: receipt,
                    effect_id: effect.id,
                    content_digest: effect.content_digest,
                },
                spec: spec.clone(),
            })
            .await
            .unwrap();
        spec.did
    }
}

impl Harness {
    pub async fn cancel(&self) {
        let claim = self
            .f
            .coordination
            .current(ShardId(1))
            .await
            .unwrap()
            .unwrap()
            .claim;
        let AcquireOutcome::Acquired(current) = self
            .f
            .coordination
            .acquire(
                &processor_support::spec().sid,
                &claim,
                crate::support::initial(),
            )
            .await
            .unwrap()
        else {
            panic!("acquired")
        };
        let sequence = CommitSeq(current.envelope.checkpoint.commit_seq.0 + 1);
        let intent = CancelIntent {
            request_id: RequestId("cancel".into()),
            reason: Some("user".into()),
        };
        let kind = EffectKind::RequestCancel {
            did: self.did.clone(),
            intent: intent.clone(),
        };
        let effect = Effect {
            id: EffectId {
                sid: processor_support::spec().sid,
                commit: sequence,
                index: NonZeroU32::MIN,
            },
            content_digest: digest_json(&serde_json::to_value(&kind).unwrap()),
            kind,
        };
        let commit_id = CommitId("cancel".into());
        let mut checkpoint = current.envelope.checkpoint;
        checkpoint.commit_seq = sequence;
        checkpoint.outbox = Some(CommittedBatch {
            created_at: None,
            commit_id: commit_id.clone(),
            source_commit: sequence,
            content_digest: Digest(String::new()),
            effects: vec![effect.clone()],
        });
        let digest = digest_json(&serde_json::to_value(&checkpoint).unwrap());
        checkpoint.outbox.as_mut().unwrap().content_digest = digest.clone();
        let CommitOutcome::Committed(receipt) = self
            .f
            .coordination
            .commit(CommitRequest {
                purpose: CommitPurpose::Ordinary {
                    control_revision: 0,
                },
                owner: current.ownership,
                expected_commit_seq: CommitSeq(sequence.0 - 1),
                commit_id,
                content_digest: digest,
                candidate: checkpoint,
            })
            .await
            .unwrap()
        else {
            panic!("committed")
        };
        self.f
            .dispatch
            .request_cancel(CommittedCancelIntent {
                source: EffectSource {
                    commit: receipt,
                    effect_id: effect.id,
                    content_digest: effect.content_digest,
                },
                did: self.did.clone(),
                intent,
            })
            .await
            .unwrap();
    }
}
