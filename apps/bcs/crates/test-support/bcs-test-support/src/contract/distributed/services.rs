use super::ContractResult;
use bcs_dist_model::*;
use bcs_service_api::{
    application::{dist_gateway::*, dist_runtime::*},
    core::{dist_gateway::*, dist_runner::*},
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
