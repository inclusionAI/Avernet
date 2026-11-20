use super::*;
use bcs_domain::caller_identity_material::{
    AuthenticatedHumanCallerIngress, CallerSubjectRefType, HumanCallerAuthSource,
};

use bcs_service_api::NativeCallerCredential;

pub(super) struct DispatchProof {
    pub proof: ProofToken,
    pub identity_deadline: i64,
    pub proof_type: String,
    pub lineage_subject: Option<(String, CallerSubjectRefType)>,
}

impl CallerDispatchPipeline {
    pub(super) async fn prepare_dispatch_proof(
        &self,
        facts: &CommittedDispatchFacts,
        ingress: &AuthenticatedHumanCallerIngress,
        descriptor: &bcs_service_api::port::CallerContainerDescriptor,
        effective_deadline: i64,
        now_ms: i64,
        native: Option<NativeCallerCredential>,
    ) -> Result<DispatchProof, CallerDispatchError> {
        if let Some(native) = native {
            if ingress.auth_source() != HumanCallerAuthSource::ChannelIngress
                || native.user_id != ingress.actor().staff_no
                || native.proof.expires_at_ms() <= now_ms
            {
                return Err(CallerDispatchError::InvalidInput);
            }
            return Ok(DispatchProof {
                identity_deadline: native.proof.expires_at_ms(),
                proof: native.proof,
                proof_type: native.token_type,
                // Channel credentials only authorize this root message. They
                // must never become a parent token for delegated Bot work.
                lineage_subject: None,
            });
        }

        tracing::debug!(dispatch_id = %facts.dispatch_id, stage = "establish_subject", "caller protected dispatch stage");
        let established = match stage_bound(
            self.stage_timeout,
            "establish_subject",
            self.identity
                .establish_subject(bcs_caller_identity_api::EstablishCallerSubject {
                    ingress: ingress.clone(),
                    now_ms,
                }),
        )
        .await
        {
            Some(Ok(established)) => established,
            Some(Err(error)) => {
                tracing::warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    dispatch_id = %facts.dispatch_id,
                    error = ?error,
                    "caller identity plugin refused to establish the subject"
                );
                return Err(CallerDispatchError::EstablishFailed);
            }
            None => {
                return Err(CallerDispatchError::StageTimeout {
                    stage: "establish_subject",
                });
            }
        };
        // Advisory caller-context lifetime for the wire envelope; never a
        // rejection basis.
        let identity_deadline = established.expires_at_ms;
        let identity_subject = established.subject_ref.expose_ref().to_string();
        let identity_subject_type = established.subject_ref.ref_type();
        let subject_ref = established.subject_ref;
        // The registry lookup is in-process and fast, but the spec counts the
        // `target_agent_code` hunt inside the issue stage's liveness bound so
        // a wedged registry port cannot stretch the budget unmeasured.
        let agent_code = match stage_bound(
            self.stage_timeout,
            "issue_execution",
            self.target_agent_code(&facts.target_bot_id),
        )
        .await
        {
            Some(code) => code,
            None => {
                return Err(CallerDispatchError::StageTimeout {
                    stage: "issue_execution",
                });
            }
        };
        let issue_input = IssueCallerExecution {
            staff_no: opaque_from(ingress.actor()),
            agent_code,
            sandbox_container_id: descriptor.sandbox_container_id.clone(),
            subject: subject_ref,
            run: bcs_caller_identity_api::CallerRunBinding {
                run_id: facts.run_id.clone(),
                root_run_id: facts.root_run_id.clone(),
                parent_run_id: None,
                message_turn_id: Some(facts.message_turn_id.clone()),
                attempt: facts.attempt,
            },
            scope: bcs_caller_identity_api::CallerProofScope {
                env: facts.environment.clone(),
                target_bot_id: facts.target_bot_id.clone(),
                group_id: facts.group_id.clone(),
                session_id: facts.session_id.clone(),
            },
            delegation: bcs_caller_identity_api::CallerDelegationBounds {
                depth: 0,
                max_depth: if facts.group_id.is_some() { 2 } else { 0 },
            },
            purpose:
                bcs_service_api::application::caller_identity::CallerExecutionPurpose::Execution,
            not_after_ms: effective_deadline,
            isolation: descriptor.binding_ref.as_ref().map(|binding| {
                bcs_caller_identity_api::CallerIsolationConstraint {
                    binding_ref: binding.expose().to_string(),
                    generation: descriptor.runtime_generation,
                }
            }),
            source: IssueCallerExecutionSource::HumanOriginHandle,
            now_ms,
        };
        tracing::debug!(dispatch_id = %facts.dispatch_id, stage = "issue_execution", "caller protected dispatch stage");
        let proof: ProofToken = match stage_bound(
            self.stage_timeout,
            "issue_execution",
            self.identity.issue_execution(issue_input),
        )
        .await
        {
            Some(Ok(token)) => token,
            Some(Err(error)) => {
                tracing::warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    dispatch_id = %facts.dispatch_id,
                    error = ?error,
                    "caller identity plugin refused to issue an execution proof"
                );
                return Err(CallerDispatchError::IssueFailed);
            }
            None => {
                return Err(CallerDispatchError::StageTimeout {
                    stage: "issue_execution",
                });
            }
        };

        Ok(DispatchProof {
            proof,
            identity_deadline,
            proof_type: self.identity.proof_type().to_string(),
            lineage_subject: Some((identity_subject, identity_subject_type)),
        })
    }
}
