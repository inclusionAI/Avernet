//! Reusable phase-one resource/control assertions against actual providers.
use super::ContractResult;
use bcs_dist_model::*;
use bcs_service_api::{
    application::dist_runtime::DistWorkerIsolationService,
    core::dist_projection::OutputProjectionCoreService,
    port::dist_runtime::*,
    port::repo::distributed::{admission::*, budget::*, common::SessionOwnership, control::*},
    types::dist_runtime::WorkerControlAction,
};

/// Fresh budget: every declared permit is available, and releases restore it.
pub fn scan_budget_port_contract_tests(port: &dyn ScanBudgetPort) {
    let profile = port.profile();
    assert!(
        profile.io_slots > 0 && profile.io_slots <= 8 && profile.io_slots <= profile.pool_size / 4
    );
    let permits = (0..profile.io_slots)
        .map(|_| port.try_io().expect("declared permit"))
        .collect::<Vec<_>>();
    assert!(port.try_io().is_none());
    drop(permits);
    assert!(port.try_io().is_some());
}
/// Fixture contains one healthy retained input; calls must complete a fixed cut.
pub async fn admission_maintenance_port_contract_tests(
    port: &dyn AdmissionMaintenancePort,
    shard: ShardId,
) -> ContractResult {
    let mut repair = false;
    let mut discovery = false;
    for _ in 0..32 {
        let r = port.repair(shard).await?;
        let d = port.discover(shard).await?;
        assert!(r.examined <= 128 && d.examined <= 128);
        repair |= r.round_complete && r.pending == 0;
        discovery |= d.round_complete && d.pending == 0;
        if repair && discovery {
            return Ok(());
        }
    }
    panic!("healthy retained cut did not finish")
}
/// A real closed gate and original owner must remain stable on repeated reads.
pub async fn admission_control_port_contract_tests(
    port: &dyn AdmissionControlPort,
    owner: &SessionOwnership,
    closure: &AdmissionClosure,
) -> ContractResult {
    let state = port.gate_state(owner).await?;
    assert_eq!(state.closure.as_ref(), Some(closure));
    assert_eq!(state.closed_operation.as_ref(), Some(&closure.operation_id));
    assert_eq!(port.gate_state(owner).await?, state);
    Ok(())
}
/// Empty, durably closed fixture: no terminal task is needed to certify its cut.
pub async fn admission_closure_port_contract_tests(
    port: &dyn AdmissionClosurePort,
    owner: &SessionOwnership,
    closure: &AdmissionClosure,
) -> ContractResult {
    for _ in 0..16 {
        let work = port.advance_closure(owner, closure).await?;
        if let Some(proof) = work.certificate {
            assert_eq!(&proof.closure, closure);
            assert!(proof.completed_through >= closure.journal_cut);
            assert!(!proof.evidence_digest.0.is_empty());
            return Ok(());
        }
        assert!(
            work.obligation.is_none(),
            "fixture must have no accepted tasks"
        );
    }
    panic!("empty closure did not finish")
}
pub async fn terminal_task_evidence_port_contract_tests(
    port: &dyn TerminalTaskEvidencePort,
    scope: &TaskScope,
    tid: &Tid,
    expected: Option<TerminalTaskEvidence>,
) -> ContractResult {
    assert_eq!(port.terminal_evidence(scope, tid).await?, expected);
    assert_eq!(port.terminal_evidence(scope, tid).await?, expected);
    Ok(())
}
/// Original guarded identity is retained; its repeat cannot replace the head.
pub async fn session_control_port_contract_tests(
    port: &dyn SessionControlPort,
    guard: ControlGuard,
    attempt: AttemptIdentity,
) -> ContractResult {
    let first = port.begin_attempt(guard.clone(), attempt.clone()).await?;
    let ControlOutcome::Applied(first) = first else {
        panic!("fresh attempt must apply")
    };
    assert_eq!(first.head, guard.expected_head);
    assert!(matches!(
        port.begin_attempt(guard.clone(), attempt.clone()).await?,
        ControlOutcome::AlreadyApplied(_)
    ));
    assert_eq!(
        port.get_attempt(&guard.owner.sid, &attempt.id)
            .await?
            .expect("retained attempt")
            .identity,
        attempt
    );
    Ok(())
}
pub fn output_projection_core_service_contract_tests(port: &dyn OutputProjectionCoreService) {
    let empty = DisplaySnapshot {
        format_version: 1,
        units: vec![],
    };
    let change = ArtifactChange {
        node: NodeId("node".into()),
        artifact_id: ArtifactId("answer".into()),
        class: OutputClass::Output,
        update: ArtifactUpdate::Upsert {
            content: Payload::Inline(serde_json::json!("hello")),
        },
    };
    let changes = [change];
    let a = port
        .project_candidate(
            &empty,
            OutputUsage::default(),
            &changes,
            &PHASE_ONE_OUTPUT_LIMITS,
        )
        .expect("valid projection");
    assert_eq!(
        port.fold_committed(&empty, &changes, &PHASE_ONE_OUTPUT_LIMITS)
            .expect("fold"),
        a.snapshot
    );
    assert_eq!(
        port.project_candidate(
            &empty,
            OutputUsage::default(),
            &changes,
            &PHASE_ONE_OUTPUT_LIMITS
        )
        .expect("repeat"),
        a
    );
}
/// Fixture policy grants inspect but denies recovery for this actor/target pair.
pub async fn dist_worker_control_authorizer_port_contract_tests(
    port: &dyn DistWorkerControlAuthorizerPort,
    actor: &TaskScope,
    target: &TaskScope,
) -> ContractResult {
    assert!(
        port.authorize(actor, target, WorkerControlAction::Inspect)
            .await?
    );
    assert!(
        !port
            .authorize(actor, target, WorkerControlAction::Resume)
            .await?
    );
    Ok(())
}
/// Wrong task scope cannot read another Session's isolation state.
pub async fn dist_worker_isolation_control_port_contract_tests(
    port: &dyn DistWorkerIsolationControlPort,
    wrong: &TaskScope,
    sid: &Sid,
) -> ContractResult {
    assert!(port.isolation(wrong, sid).await?.is_none());
    Ok(())
}
/// Unauthorized actor is rejected before the administrative engine is invoked.
pub async fn dist_worker_isolation_service_contract_tests(
    port: &dyn DistWorkerIsolationService,
    actor: TaskScope,
    target: TaskScope,
    sid: Sid,
) -> ContractResult {
    assert_eq!(
        port.isolation(actor, target, sid)
            .await
            .expect_err("authorization required")
            .code,
        "worker_control_forbidden"
    );
    Ok(())
}
