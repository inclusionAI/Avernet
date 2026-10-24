//! The Workbench output-admission port (bot-authority spec §8.2:
//! 许可在业务副作用前建立，由应用层判定、适配层安装).
//!
//! At business-accept time the application asks this port to freeze,
//! per currently-subscribed connection of the operation's canonical
//! scope, that connection's delivery view into a read-only permit.
//! Everything afterwards — fanout frames, run-sink frames, aliases —
//! checks the permit locally; a mid-operation revocation changes
//! only FUTURE admissions, never this operation's installed output.
//!
//! Failure policy (spec §6.1/§6.2): the admission DENIAL of a
//! passive bystander never rolls back an accepted operation, and an
//! underlying resolution error is absorbed HERE (warn + zero
//! permits for this operation) — it must neither fail the business
//! send nor cancel any permit installed earlier.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::v1::{DeliveryAdmissionService, DeliveryScope};
use bcs_service_api::port::{OutputAdmissionPort, OutputOperationAdmission};
use bcs_service_api::ServiceResult;
use tracing::warn;

use crate::shared::output_permits::{OutputPermitStore, PermitInstallOutcome};

use super::WorkbenchConnectionRegistry;

/// Output admission over the live Workbench connection registry.
pub struct WorkbenchOutputAdmission {
    connections: Arc<WorkbenchConnectionRegistry>,
    permits: Arc<OutputPermitStore>,
    admission: Arc<dyn DeliveryAdmissionService>,
}

impl WorkbenchOutputAdmission {
    pub fn new(
        connections: Arc<WorkbenchConnectionRegistry>,
        admission: Arc<dyn DeliveryAdmissionService>,
    ) -> Self {
        Self {
            permits: connections.output_permits(),
            connections,
            admission,
        }
    }
}

#[async_trait]
impl OutputAdmissionPort for WorkbenchOutputAdmission {
    async fn admit_output_operation(
        &self,
        admission: OutputOperationAdmission,
    ) -> ServiceResult<()> {
        let scope_key = admission
            .session_id
            .clone()
            .unwrap_or_else(|| admission.group_id.clone());
        let candidates = self
            .connections
            .protected_candidates(&scope_key)
            .await;
        if candidates.is_empty() {
            return Ok(());
        }
        let scope = DeliveryScope {
            group_id: admission.group_id.clone(),
            session_id: admission.session_id.clone(),
        };
        let recipients = match self
            .admission
            .resolve_recipient_views(&scope, &candidates)
            .await
        {
            Ok(recipients) => recipients,
            Err(error) => {
                // A storage read failure is recorded, never mistaken
                // for "no role": this operation flows with zero
                // permits while every earlier permit is untouched.
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    group_id = %admission.group_id,
                    session_id = ?admission.session_id,
                    %error,
                    "output admission read failed; operation proceeds without fresh permits"
                );
                return Ok(());
            }
        };
        match self
            .permits
            .install_permits(&admission, recipients)
            .await
        {
            PermitInstallOutcome::Installed => Ok(()),
            PermitInstallOutcome::RefusedCapacity => {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    group_id = %admission.group_id,
                    session_id = ?admission.session_id,
                    "output permit store at capacity; operation proceeds without fresh permits"
                );
                Ok(())
            }
        }
    }

    async fn register_operation_runs(
        &self,
        operation_id: &str,
        run_ids: Vec<String>,
    ) -> ServiceResult<()> {
        // The application registers the operation's COMPLETE stream
        // set before any downstream callback can reference one of the
        // runs: Run terminal ≠ operation complete — the operation's
        // output closes when every registered stream is terminal.
        self.permits
            .register_operation_runs(operation_id, &run_ids)
            .await;
        Ok(())
    }

    async fn release_operation_runs(
        &self,
        operation_id: &str,
        run_ids: Vec<String>,
    ) -> ServiceResult<()> {
        // Explicit end-state for streams that will never produce
        // output (transport failure, admission rejection, aborted
        // send): tombstone them so the operation can complete
        // without waiting on a stream that is provably over.
        self.permits
            .release_operation_runs(operation_id, &run_ids)
            .await;
        Ok(())
    }
}
