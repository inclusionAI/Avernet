//! A wait whose position keeps being unusable pauses between attempts instead
//! of spinning: the number of task reads is bounded by the pause, not the CPU.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]

use async_trait::async_trait;
use bcs_app_dist_gateway::{Clock, DistGateway, DistGatewayConfig};
use bcs_dist_model::*;
use bcs_service_api::application::dist_gateway::*;
use bcs_service_api::application::principal::{BotActor, CallerContext};
use bcs_service_api::core::dist_gateway::*;
use bcs_service_api::port::dist_agent::*;
use futures::StreamExt;
use futures::stream::BoxStream;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

const SID: &str = "s0042.7f3a9c";
const TID: &str = "s0042.7f3a9c.b21e";

fn group() -> SessionTarget {
    SessionTarget::Group { group: GroupId("grp-research-01".into()) }
}

fn scope() -> TaskScope {
    TaskScope {
        tenant: TenantId("t-ant".into()),
        principal: PrincipalId("bot:pa-001".into()),
    }
}

/// A core whose task never finishes and whose every follow is a gap at once.
#[derive(Default)]
struct AlwaysGap {
    restores: AtomicUsize,
    follows: AtomicUsize,
}

#[async_trait]
impl DistGatewayCoreService for AlwaysGap {
    async fn admit(&self, _: &TaskScope, _: AdmitTaskCommand) -> Result<CoreAdmission, DistGatewayCoreError> {
        Err(DistGatewayCoreError::NotFound)
    }
    async fn session_target(&self, _: &TaskScope, _: &Sid) -> Result<SessionTarget, DistGatewayCoreError> {
        Ok(group())
    }
    async fn restore_task(&self, _: &TaskScope, _: &Tid) -> Result<RestoredTaskCut, DistGatewayCoreError> {
        self.restores.fetch_add(1, Ordering::SeqCst);
        let cursor = TaskCursor {
            sid: Sid(SID.into()),
            tid: Tid(TID.into()),
            token: ReplayToken {
                backend: BackendId("tbase".into()),
                generation: LogGeneration("1".into()),
                value: "1-1".into(),
            },
        };
        Ok(RestoredTaskCut {
            view: TaskView {
                scope: scope(),
                sid: Sid(SID.into()),
                tid: Tid(TID.into()),
                content: TaskContent {
                    status: TaskStatus::Working,
                    interactions: vec![],
                    snapshot: Payload::Inline(serde_json::Value::Null),
                    result_ref: None,
                },
                covered_through: None,
            },
            cursor: Some(cursor),
        })
    }
    async fn validate_resume(&self, _: &TaskScope, _: &TaskCursor) -> Result<CoreResumeCheck, DistGatewayCoreError> {
        Ok(CoreResumeCheck::Gap)
    }
    fn follow_task(&self, _: TaskScope, _: TaskCursor) -> BoxStream<'static, Result<CoreFollowItem, DistGatewayCoreError>> {
        self.follows.fetch_add(1, Ordering::SeqCst);
        futures::stream::iter([Ok(CoreFollowItem::Gap)]).boxed()
    }
    async fn list_tasks(&self, _: &TaskScope, _: CoreTaskQuery) -> Result<CoreTaskPage, DistGatewayCoreError> {
        Err(DistGatewayCoreError::NotFound)
    }
}

struct NoGroups;
#[async_trait]
impl DistSessionDirectoryPort for NoGroups {
    async fn authorize(&self, _: &TaskScope, _: &SessionTarget) -> Result<(), DirectoryError> {
        Ok(())
    }

    async fn resolve(&self, _: &TaskScope, _: &SessionTarget) -> Result<FrozenSessionProposal, DirectoryError> {
        Err(DirectoryError::NotFound)
    }

}

struct TestClock;
impl Clock for TestClock {
    fn now(&self) -> TimestampMillis {
        TimestampMillis(0)
    }
}

#[tokio::test]
async fn a_wait_that_keeps_hitting_gaps_pauses_between_attempts() {
    let core = Arc::new(AlwaysGap::default());
    let mut config = DistGatewayConfig::new(64);
    config.snapshot_cover_retry = Duration::from_millis(50);
    let gateway = DistGateway::new(core.clone(), Arc::new(NoGroups), Arc::new(TestClock), config).unwrap();
    let caller = GatewayCaller {
        tenant: TenantId("t-ant".into()),
        caller: CallerContext::Bot(BotActor {
            bot_uuid: "pa-001".into(),
        }),
    };
    let wait = WaitTask {
        target: group(),
        tid: Tid(TID.into()),
        until: WaitUntil::Final,
        after: None,
        timeout: Duration::from_millis(400),
    };
    let started = std::time::Instant::now();
    let view = gateway.wait_task(&caller, wait).await.unwrap();

    // At the deadline the current view is returned.
    assert_eq!(view.content.status, TaskStatus::Working);
    assert!(started.elapsed() >= Duration::from_millis(300), "the wait runs to its deadline");
    assert!(started.elapsed() < Duration::from_secs(3));
    // 400 ms with a 50 ms pause allows about 8 attempts. Without the pause the
    // loop would make many thousands in that time.
    let follows = core.follows.load(Ordering::SeqCst);
    let restores = core.restores.load(Ordering::SeqCst);
    assert!((2..=12).contains(&follows), "follow attempts: {follows}");
    assert!(restores <= 14, "task reads: {restores}");
}
