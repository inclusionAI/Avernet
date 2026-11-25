#![allow(clippy::unwrap_used, reason = "authorization boundary assertions")]
use async_trait::async_trait;
use bcs_app_dist_runtime::WorkerIsolationApplication;
use bcs_dist_model::*;
use bcs_service_api::{
    application::dist_runtime::DistWorkerIsolationService,
    port::{dist_runtime::*, repo::distributed::control::*},
    types::dist_runtime::*,
};
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};
struct Engine(AtomicUsize);
#[async_trait]
impl DistWorkerIsolationControlPort for Engine {
    async fn isolation(
        &self,
        _: &TaskScope,
        _: &Sid,
    ) -> Result<Option<ControlSnapshot>, RuntimeError> {
        self.0.fetch_add(1, Ordering::SeqCst);
        Ok(None)
    }
    async fn resume(
        &self,
        _: &TaskScope,
        _: RecoveryAuthorization,
    ) -> Result<ControlOutcome, RuntimeError> {
        self.0.fetch_add(1, Ordering::SeqCst);
        Ok(ControlOutcome::NotReady)
    }
}
struct Policy;
#[async_trait]
impl DistWorkerControlAuthorizerPort for Policy {
    async fn authorize(
        &self,
        actor: &TaskScope,
        target: &TaskScope,
        action: WorkerControlAction,
    ) -> Result<bool, RuntimeError> {
        Ok(actor.tenant == target.tenant
            && actor.principal.0 == "admin"
            && action == WorkerControlAction::Inspect)
    }
}
fn scope(principal: &str) -> TaskScope {
    TaskScope {
        tenant: TenantId("tenant".into()),
        principal: PrincipalId(principal.into()),
    }
}
#[tokio::test]
async fn isolation_query_requires_authorization_before_engine_lookup() {
    bcs_test_support::contract::distributed::runtime_safety::dist_worker_control_authorizer_port_contract_tests(&Policy,&scope("admin"),&scope("owner")).await.unwrap();
    let engine = Arc::new(Engine(AtomicUsize::new(0)));
    let app = WorkerIsolationApplication::new(engine.clone(), Arc::new(Policy));
    assert_eq!(
        app.isolation(scope("user"), scope("owner"), Sid("s0001.session".into()))
            .await
            .unwrap_err()
            .code,
        "worker_control_forbidden"
    );
    bcs_test_support::contract::distributed::runtime_safety::dist_worker_isolation_service_contract_tests(&app,scope("user"),scope("owner"),Sid("s0001.session".into())).await.unwrap();
    assert_eq!(engine.0.load(Ordering::SeqCst), 0);
    assert!(
        app.isolation(scope("admin"), scope("owner"), Sid("s0001.session".into()))
            .await
            .unwrap()
            .is_none()
    );
    assert_eq!(engine.0.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn inspection_permission_does_not_authorize_resume() {
    use bcs_service_api::port::repo::distributed::{admission::*, common::*};
    let engine = Arc::new(Engine(AtomicUsize::new(0)));
    let app = WorkerIsolationApplication::new(engine.clone(), Arc::new(Policy));
    let sid = Sid("s0001.session".into());
    let request = RecoveryAuthorization {
        guard: ControlGuard {
            owner: SessionOwnership {
                sid: sid.clone(),
                shard: ShardId(1),
                owner: ShardOwner {
                    instance: Instance {
                        node: "worker".into(),
                        incarnation: "one".into(),
                    },
                    epoch: WorkerEpoch(1),
                },
            },
            expected_head: CommitSeq(1),
            expected_revision: 1,
        },
        isolation_id: RequestId("isolation".into()),
        request_id: RequestId("resume".into()),
        completion: ClosureCertificate {
            closure: AdmissionClosure {
                sid,
                generation: 1,
                operation_id: RequestId("isolation".into()),
                journal_cut: 0,
            },
            completed_through: 0,
            terminal_tasks: 0,
            evidence_digest: Digest("verified".into()),
        },
    };
    assert_eq!(
        app.resume(scope("admin"), scope("owner"), request)
            .await
            .unwrap_err()
            .code,
        "worker_control_forbidden"
    );
    assert_eq!(engine.0.load(Ordering::SeqCst), 0);
}
