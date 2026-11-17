use super::ContractResult;
use bcs_dist_model::*;
use bcs_service_api::{
    application::{dist_gateway::*, dist_runtime::*},
    core::{dist_connector::*, dist_gateway::*, dist_runner::*},
    port::dist_agent::*,
    port::dist_runtime::DistRuntimeControlPort,
};
use std::time::Duration;

pub fn dist_runner_core_service_contract_tests(
    runner: &dyn DistRunnerCoreService,
    context: &EvaluationContext<'_>,
    state: &RunnerState,
    inputs: &[RunnerInput],
) -> ContractResult {
    assert_eq!(runner.init(context)?, runner.init(context)?);
    assert_eq!(
        runner.reduce(context, state, inputs)?,
        runner.reduce(context, state, inputs)?
    );
    Ok(())
}

pub async fn dist_gateway_core_service_contract_tests(
    core: &dyn DistGatewayCoreService,
    scope: &TaskScope,
    request: AdmitTaskCommand,
) -> ContractResult {
    let created = request.create_session.clone();
    let CoreAdmission(receipt) = core.admit(scope, request.clone()).await?;
    assert_eq!(core.admit(scope, request).await?, CoreAdmission(receipt.clone()));
    if let Some(spec) = created {
        // The session keeps the group it was created for, and is absent for
        // another scope.
        assert_eq!(core.session_group(scope, &receipt.sid).await?, spec.group);
        let stranger = TaskScope {
            tenant: scope.tenant.clone(),
            principal: PrincipalId(format!("{}-another", scope.principal.0)),
        };
        assert_eq!(
            core.session_group(&stranger, &receipt.sid).await,
            Err(DistGatewayCoreError::NotFound)
        );
    }
    Ok(())
}

pub async fn dist_gateway_service_contract_tests(
    gateway: &dyn DistGatewayService,
    caller: &GatewayCaller,
    unauthorized: &GatewayCaller,
    request: SubmitInput,
) -> ContractResult {
    let group = request.group.clone();
    let receipt = gateway.submit(caller, request.clone()).await?;
    assert_eq!(gateway.submit(caller, request).await?, receipt);
    assert_eq!(
        gateway.get_task(caller, &group, &receipt.tid).await?.tid,
        receipt.tid
    );
    assert!(matches!(
        gateway.get_task(unauthorized, &group, &receipt.tid).await,
        Err(GatewayError::AccessDenied | GatewayError::NotFound)
    ));
    // A task is reached only through the group it was created for.
    let another = GroupId(format!("{}-another", group.0));
    assert_eq!(
        gateway.get_task(caller, &another, &receipt.tid).await,
        Err(GatewayError::NotFound)
    );
    Ok(())
}

/// Isolated idle runtime. Draining changes state without fabricating unfinished work.
pub async fn dist_worker_runtime_service_contract_tests(
    runtime: &dyn DistWorkerRuntimeService,
) -> ContractResult {
    assert_eq!(runtime.snapshot().await?.active_work, 0);
    assert_eq!(
        runtime.drain(Duration::from_secs(1)).await?,
        DrainOutcome::Drained
    );
    assert!(runtime.snapshot().await?.draining);
    Ok(())
}

pub async fn dist_connector_runtime_service_contract_tests(
    runtime: &dyn DistConnectorRuntimeService,
) -> ContractResult {
    assert_eq!(runtime.snapshot().await?.active_work, 0);
    assert_eq!(
        runtime.drain(Duration::from_secs(1)).await?,
        DrainOutcome::Drained
    );
    assert!(runtime.snapshot().await?.draining);
    Ok(())
}

pub async fn dist_runtime_control_port_contract_tests(
    runtime: &dyn DistRuntimeControlPort,
) -> ContractResult {
    assert_eq!(runtime.snapshot().await?.active_work, 0);
    assert_eq!(
        runtime.drain(Duration::from_secs(1)).await?,
        DrainOutcome::Drained
    );
    assert!(runtime.snapshot().await?.draining);
    Ok(())
}

/// Fixture adapter scripts definite pre-send failure, ambiguous send, cancel timeout.
pub async fn dist_agent_client_port_contract_tests(
    client: &dyn DistAgentClientPort,
    target: &AgentBinding,
    not_sent: StableSendRequest,
    ambiguous: StableSendRequest,
    remote: &RemoteTaskRef,
    cancellation_id: RequestId,
) -> ContractResult {
    assert!(matches!(
        client.start(target, not_sent).await,
        SendOutcome::NotSent { .. }
    ));
    assert!(matches!(
        client.start(target, ambiguous).await,
        SendOutcome::OutcomeUnknown { .. }
    ));
    assert!(matches!(
        client.cancel(target, remote, cancellation_id).await,
        CancelOutcome::OutcomeUnknown { .. }
    ));
    Ok(())
}

pub async fn dist_group_directory_port_contract_tests(
    directory: &dyn DistGroupDirectoryPort,
    scope: &TaskScope,
    group: &GroupId,
) -> ContractResult {
    let first = directory.resolve(scope, group).await?;
    assert_eq!(&first.group, group);
    assert_eq!(directory.resolve(scope, group).await?, first);
    let workflow = directory.load_workflow(scope, &first.workflow).await?;
    assert_eq!(
        serde_json::to_value(workflow)?,
        serde_json::to_value(directory.load_workflow(scope, &first.workflow).await?)?
    );
    Ok(())
}

/// The fixture is an accepted dispatch. Identities must not depend on the
/// holder generation, and the sequence restarts with every cursor.
pub fn dist_connector_core_service_contract_tests(
    rules: &dyn DistConnectorCoreService,
    view: &DispatchView,
) -> ContractResult {
    let did = &view.spec.did;
    assert_eq!(rules.start_request_id(did), rules.start_request_id(did));
    assert_ne!(
        rules.input_request_id(did, InputSeq(1)),
        rules.input_request_id(did, InputSeq(2))
    );
    let payload = Payload::Inline(serde_json::json!({"state": "completed"}));
    let mut first = ReportCursor { holder_epoch: HolderEpoch(1), last_seq: InputSeq(0) };
    let mut later = ReportCursor { holder_epoch: HolderEpoch(2), last_seq: InputSeq(0) };
    let delta = rules.report(view, &mut first, AgentEventKind::Delta, None, payload.clone());
    match delta {
        Report::Stream(append) => {
            assert_eq!(append.event.seq, InputSeq(1));
            assert_eq!(append.event.holder_epoch, HolderEpoch(1));
        }
        Report::Key(_) => panic!("a delta is not a key event"),
    }
    let a = rules.report(view, &mut first, AgentEventKind::Final, Some("e-1".into()), payload.clone());
    let b = rules.report(view, &mut later, AgentEventKind::Final, None, payload.clone());
    match (a, b) {
        (Report::Key(a), Report::Key(b)) => {
            assert_eq!(a.input.input_id, b.input.input_id);
            assert_eq!(a.input.business_digest, b.input.business_digest);
            assert!(a.create_session.is_none() && a.create_task.is_none());
        }
        _ => panic!("a final event must be admitted"),
    }
    assert_eq!(first.last_seq, InputSeq(2));
    assert_eq!(later.last_seq, InputSeq(1));
    assert_eq!(
        rules.end_of(&AgentEventKind::Final, &payload),
        Some(FinalDispatchOutcome::Completed)
    );
    assert_eq!(rules.end_of(&AgentEventKind::Delta, &payload), None);
    Ok(())
}
