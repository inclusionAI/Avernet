//! Stable caller intent and receipt recovery at the Worker admission boundary.
use super::ContractResult;
use bcs_dist_model::*;
use bcs_service_api::{
    core::dist_admission::{TaskAdmissionCommand, TaskAdmissionCoreService},
    port::repo::distributed::inbox::AdmitOutcome,
};

/// Supply an isolated new task command and a different body for the same ID.
/// A changed resolved workflow is deliberately not a changed caller request.
pub async fn task_admission_core_service_contract_tests(
    service: &dyn TaskAdmissionCoreService,
    scope: &TaskScope,
    command: TaskAdmissionCommand,
    different_body: InputBody,
) -> ContractResult {
    assert_ne!(command.input.body, different_body);
    let AdmitOutcome::Accepted(receipt) = service.admit(scope, command.clone()).await? else {
        panic!("isolated first admission must be Accepted");
    };
    let mut retry = command.clone();
    if let Some(definition) = &mut retry.create_session {
        definition
            .mode_envelope
            .revision
            .push_str("-changed-after-acceptance");
    }
    assert_eq!(
        service.admit(scope, retry).await?,
        AdmitOutcome::Duplicate(receipt),
    );
    let mut changed = command;
    changed.input.body = different_body;
    assert_eq!(
        service.admit(scope, changed).await?,
        AdmitOutcome::ContentConflict
    );
    Ok(())
}
