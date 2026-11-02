//! A TaskDispatch is a new operation, not an extension of its Manager's Run.
//! Freeze recipients and Worker facts at acceptance, before any output can race
//! admission. The caller's Group already contains the authorized Session rows.
use super::*;
use bcs_service_api::port::repo::message_delivery::{AdmitMessageDeliveries, MessageDeliveryRepoError};
use bcs_service_api::{ManagedDeliveryError, OutputOperationAdmission};
use bcs_domain::message_delivery::{MessageDeliveryStatus, PersistedMessageDelivery};

pub(super) async fn admit_output(
    flow: &BcsMessageFlow, group: &Group, session: &str, run: &str, worker: &str,
) -> ServiceResult<String> {
    let operation = uuid::Uuid::new_v4().to_string();
    if let Some(port) = &flow.output_admission {
        port.admit_output_operation(OutputOperationAdmission {
            operation_id: operation.clone(), group_id: group.id.clone(),
            session_id: (session != group.id).then(|| session.to_string()),
        }).await?;
    }
    flow.message_tracker.cache_run_operation(run, &operation).await;
    // No extra Session read: dispatch already applied its accepted participants.
    crate::group_flow::freeze_run_output_facts(flow, group, None, run, worker).await;
    if let Some(port) = &flow.output_admission {
        if let Err(error) = port.register_operation_runs(&operation, vec![run.to_string()]).await {
            flow.release_output_runs(&operation, &[run.to_string()]).await;
            return Err(error);
        }
    }
    Ok(operation)
}

pub(super) async fn admit_queued_dispatch(
    flow: &BcsMessageFlow, group: &Group, mut command: AdmitMessageDeliveries,
) -> ServiceResult<Option<PersistedMessageDelivery>> {
    let service = flow.managed_deliveries.as_ref()
        .ok_or_else(|| crate::queued_task::error("task queue unavailable"))?;
    let target = command.targets.first_mut()
        .ok_or_else(|| crate::queued_task::error("task queue target missing"))?;
    let run = uuid::Uuid::new_v4().to_string();
    target.run_id = Some(run.clone());
    let operation = admit_output(flow, group, &command.message.session_id, &run, &target.target_bot_id).await?;
    // The scheduler can emit before admit returns (or after a lost COMMIT ACK).
    // Registration and immutable Run identity must therefore precede commit.
    let admitted = match service.admit(command).await {
        Ok(admitted) => admitted,
        Err(error) => {
            if matches!(error, ManagedDeliveryError::Conflict | ManagedDeliveryError::Repository(
                MessageDeliveryRepoError::Conflict | MessageDeliveryRepoError::Invalid(_))) {
                flow.release_output_runs(&operation, &[run]).await;
            }
            return Err(crate::queued_task::error(&format!("task queue admission failed: {error}")));
        }
    };
    // A duplicate uses the original Run; a capacity rejection never starts one.
    // Neither may retain this attempt's fresh identity or replace the original.
    if admitted.duplicate || admitted.deliveries.iter().any(|row|
        matches!(row.state.status, MessageDeliveryStatus::Failed | MessageDeliveryStatus::RejectedCapacity)) {
        flow.release_output_runs(&operation, &[run]).await;
    }
    // Relay helpers need the committed row's own facts (delivery id, state
    // version, session, canonical turn) to park a handoff scope under the
    // SAME key the runtime's claim will build. A duplicate or rejected
    // admission parks nothing: hand back None and let finish_admission own
    // the failure.
    let committed = admitted
        .deliveries
        .iter()
        .find(|row| matches!(row.state.status, MessageDeliveryStatus::Queued))
        .cloned();
    crate::queued_task::finish_admission(flow, admitted).await?;
    Ok(committed)
}
