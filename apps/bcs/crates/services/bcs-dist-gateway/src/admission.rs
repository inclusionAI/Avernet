//! Admission rules: who may admit what, and when.
//!
//! | Input | Check | Failure |
//! | --- | --- | --- |
//! | new session | spec and task carry the caller's scope | `ScopeMismatch` |
//! | new task, existing session | the session exists in the caller's scope | `NotFound` / `ScopeMismatch` |
//! | reply | the task is in the caller's scope and this session, waits for input, and the interaction is open | `NotFound` / `ScopeMismatch` / `InvalidTransition` |
//! | cancel | the task is in the caller's scope and not finished | `NotFound` / `ScopeMismatch` / `InvalidTransition` |
//!
//! The write order itself belongs to the `InboxProducerPort` provider.

use crate::DistGatewayCore;
use crate::error::{admit_error, read_error};
use bcs_dist_model::*;
use bcs_service_api::core::dist_gateway::*;
use bcs_service_api::port::repo::distributed::inbox::{AdmitOutcome, AdmitRequest};
use bcs_service_api::port::repo::distributed::task_feed::RestoredTask;

pub(crate) async fn admit(
    core: &DistGatewayCore,
    scope: &TaskScope,
    command: AdmitTaskCommand,
) -> Result<CoreAdmission, DistGatewayCoreError> {
    check(core, scope, &command).await?;
    let input_id = command.input.input_id.clone();
    let request = AdmitRequest {
        input: command.input,
        create_session: command.create_session,
        create_task: command.create_task,
    };
    match core.inbox.admit(request).await {
        Ok(AdmitOutcome::Accepted(receipt)) => Ok(CoreAdmission(receipt)),
        Ok(AdmitOutcome::ContentConflict) => Err(DistGatewayCoreError::ContentConflict),
        Ok(AdmitOutcome::Quarantined) => Err(DistGatewayCoreError::Quarantined),
        Err(error) => Err(admit_error(error, &input_id)),
    }
}

async fn check(
    core: &DistGatewayCore,
    scope: &TaskScope,
    command: &AdmitTaskCommand,
) -> Result<(), DistGatewayCoreError> {
    let input = &command.input;
    match (&command.create_session, &command.create_task) {
        (Some(spec), Some(task)) => {
            if &spec.scope != scope || &task.scope != scope {
                return Err(DistGatewayCoreError::ScopeMismatch);
            }
            starts_work(&input.body)
        }
        // A session is only created together with its first task.
        (Some(_), None) => Err(DistGatewayCoreError::InvalidTransition),
        (None, Some(task)) => {
            if &task.scope != scope {
                return Err(DistGatewayCoreError::ScopeMismatch);
            }
            match core.sessions.get_spec(&input.sid).await.map_err(read_error)? {
                Some(spec) if &spec.scope == scope => starts_work(&input.body),
                Some(_) => Err(DistGatewayCoreError::ScopeMismatch),
                None => Err(DistGatewayCoreError::NotFound),
            }
        }
        (None, None) => existing_task(core, scope, input).await,
    }
}

/// Only a user message starts a task.
fn starts_work(body: &InputBody) -> Result<(), DistGatewayCoreError> {
    match body {
        InputBody::User { .. } => Ok(()),
        _ => Err(DistGatewayCoreError::InvalidTransition),
    }
}

/// An input for a task that already exists: a reply or a cancel.
async fn existing_task(
    core: &DistGatewayCore,
    scope: &TaskScope,
    input: &InputEnvelope,
) -> Result<(), DistGatewayCoreError> {
    let view = match core.tasks.restore(scope, &input.tid).await.map_err(read_error)? {
        None => return Err(DistGatewayCoreError::NotFound),
        // A finished task takes no further input.
        Some(RestoredTask::Final(_)) => return Err(DistGatewayCoreError::InvalidTransition),
        Some(RestoredTask::Live { view, .. }) => view,
    };
    if view.sid != input.sid {
        return Err(DistGatewayCoreError::ScopeMismatch);
    }
    match &input.body {
        InputBody::Cancel { .. } => Ok(()),
        InputBody::HumanReply { interaction, .. } => {
            let waiting = matches!(
                view.content.status,
                TaskStatus::InputRequired | TaskStatus::AuthRequired
            );
            let open = view.content.interactions.iter().any(|i| &i.id == interaction);
            if waiting && open {
                Ok(())
            } else {
                Err(DistGatewayCoreError::InvalidTransition)
            }
        }
        // A plain user message needs a new task; agent events are not Gateway inputs.
        InputBody::User { .. } | InputBody::Agent { .. } => {
            Err(DistGatewayCoreError::InvalidTransition)
        }
    }
}
