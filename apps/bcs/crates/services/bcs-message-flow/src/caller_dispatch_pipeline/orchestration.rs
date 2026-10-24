//! Orchestration node dispatch on the protected caller wire (spec §8.5,
//! §15.83 S1 / review-V P1-1): the CallerDispatchPipeline's implementation
//! of OrchestrationCallerDispatchPort. One node, one dispatch, reusing the
//! node's OWN run/attempt — the identity parent is the workflow INSTANCE's
//! delegation-only association, never a completed data predecessor.
//!
//! Execution order inside: instance lineage validation (group/session/
//! window) → per-attempt invocation reservation on the instance's turn
//! budget (§8.5: fan-out nodes share it, node retry = a new attempt) →
//! container ensure under the instance's subject → bounded proof in the
//! assignee's config-decided purpose → canonical digest (no managed row
//! exists here; the digest is computed over the node facts verbatim) →
//! protected send → node-run lineage registration at instance depth+1 so
//! an embedded Bot A2A continues to increment.

use std::sync::Arc;

use bcs_caller_identity_api::IssueCallerExecutionSource;

use async_trait::async_trait;

use bcs_domain::{BotDeliveryTarget, RedactedToken};
use bcs_service_api::port::CallerDispatchAck;
use bcs_service_api::application::caller_identity::{
    CallerExecutionPurpose, CallerExecutionToken, CallerProofFormat, CallerSubjectKind,
    CallerSubjectRef,
};

use crate::caller_lineage::RootLineage;

use super::CallerDispatchPipeline;

#[async_trait]
impl bcs_service_api::port::OrchestrationCallerDispatchPort for CallerDispatchPipeline {
    async fn dispatch_orchestration_node(
        &self,
        target: &BotDeliveryTarget,
        service_credential: &RedactedToken,
        policy: bcs_service_api::port::OrchestrationAssigneePolicy,
        dispatch: bcs_service_api::port::OrchestrationNodeDispatch,
    ) -> Result<CallerDispatchAck, bcs_service_api::port::OrchestrationDispatchError> {
        use bcs_service_api::port::OrchestrationDispatchError as Error;
        use bcs_service_api::port::CallerSendError;

        let at_term = self.delivery_term.as_ref().map(|v| v.load(std::sync::atomic::Ordering::SeqCst));
        let d = dispatch;
        // ---- Structural validity, before any side effect. ----
        if d.workflow_run_id.trim().is_empty()
            || d.node_id.trim().is_empty()
            || d.node_attempt <= 0
            || d.node_run_id.trim().is_empty()
            || d.environment.trim().is_empty()
            || d.group_id.trim().is_empty()
            || d.session_id.trim().is_empty()
            || d.assignee_bot_id.trim().is_empty()
            || d.caller_task.is_empty()
            || d.channel_actor_id.trim().is_empty()
            || d.now_ms <= 0
            || d.not_after_ms <= d.now_ms
        {
            return Err(Error::InvalidInput);
        }

        // ---- §8.5 identity parent: the INSTANCE association alone. ----
        let Some(index) = self.root_lineage.as_ref() else {
            return Err(Error::LineageUnavailable);
        };
        let lineage = index
            .current(&d.workflow_run_id, d.now_ms)
            .ok_or(Error::LineageUnavailable)?;
        // Scope discipline: the instance association carries the group and
        // the session of the original Human turn — a node outside that
        // boundary inherits nothing.
        if lineage.environment != d.environment || lineage.group_id != d.group_id || lineage.session_id != d.session_id {
            return Err(Error::LineageUnavailable);
        }
        // §8.5: the instance has one fixed depth; nodes register one level
        // below and the assignee's embedded A2A keeps incrementing —
        // the existing chain bound guards the whole subtree.
        let effective_not_after = d.not_after_ms.min(lineage.expires_at_ms);
        if effective_not_after <= d.now_ms {
            return Err(Error::LineageUnavailable);
        }

        // The workflow instance is normally a delegation-only Human origin
        // handle. If the association itself is execution-backed, BCS must
        // load its exact token before any container or network side effect.
        let parent_execution_token = if matches!(
            policy,
            bcs_service_api::port::OrchestrationAssigneePolicy::Execution
        ) && lineage.parent_execution
        {
            Some(self
                .root_lineage
                .as_ref()
                .and_then(|index| index.current_parent_execution(
                    &d.environment,
                    Some(&d.group_id),
                    &d.session_id,
                    &lineage.root_run_id,
                    &lineage.message_turn_id,
                    &d.assignee_bot_id,
                    &d.workflow_run_id,
                    lineage.attempt,
                    d.now_ms,
                ))
                .ok_or(Error::LineageUnavailable)?)
        } else {
            None
        };

        // ---- §8.5 per-attempt invocation reservation on the INSTANCE
        // budget (fan-out nodes share it; a retry is a NEW attempt). ----
        index
            .reserve_invocation(
                &d.workflow_run_id,
                &format!("node:{}:{}", d.node_id, d.node_attempt),
                &d.assignee_bot_id,
                d.now_ms,
            )
            .map_err(|_| Error::LineageUnavailable)?;

        // ---- Container ensure (at most one), under the INSTANCE subject. ----
        let ensure = bcs_service_api::port::EnsureCallerContainer {
            caller_subject_ref: bcs_service_api::port::CallerOpaqueRef::new(
                lineage.delegate_subject.clone(),
            ),
            target_bot_id: d.assignee_bot_id.clone(),
            session_id: d.session_id.clone(),
            environment: d.environment.clone(),
            minimum_isolation: bcs_service_api::port::CallerContainerMinimum::CallerContainer,
            idempotency_key: format!(
                "ensure-orchestration:{}:{}:{}:{}",
                d.workflow_run_id, d.node_id, d.node_attempt, d.node_run_id
            ),
            run: bcs_service_api::port::caller_container::ContainerRunAssociation {
                run_id: d.node_run_id.clone(),
                root_run_id: lineage.root_run_id.clone(),
                parent_run_id: Some(d.workflow_run_id.clone()),
                message_turn_id: lineage.message_turn_id.clone(),
                // §6.8: the node inherits the instance turn's fixed ceiling.
                history_cutoff_seq: lineage.history_cutoff_seq,
            },
            create_mode: bcs_service_api::port::EnsureCreateMode::Lazy,
            container_ttl_ms: self.container_ttl_ms,
            deadline_ms: effective_not_after,
            now_ms: d.now_ms,
        };
        // §6.8 (review-VII P1): ONLY an Execution-policy node ensures the
        // assignee's Caller container. A DelegationOnly assignee is an
        // Owner hop — no Human×Owner container, its own routing, bot_default
        // history; the issuance below keeps subject-from-lineage with no
        // binding.
        let caller_container = matches!(
            policy,
            bcs_service_api::port::OrchestrationAssigneePolicy::Execution,
        );
        let (descriptor, effective_deadline) = if caller_container {
            match self
                .preparation
                .prepare(target, service_credential, ensure, d.now_ms)
                .await
            {
                Ok(bcs_service_api::application::caller_container::PrepareOutcome::Prepared {
                    descriptor,
                    effective_deadline_ms,
                }) => {
                    let effective = effective_deadline_ms.min(effective_not_after);
                    if effective <= d.now_ms {
                        return Err(Error::PreparationFailed);
                    }
                    (Some(descriptor), effective)
                }
                _ => return Err(Error::PreparationFailed),
            }
        } else {
            (None, effective_not_after)
        };

        // ---- Observation seeding (§6.9/§10.8): the node container's
        // readback enters the shared observe directory like every other
        // ensured container — the assignee's own replies sync to its
        // containers through the same index. ----
        if let (Some(observe), Some(descriptor)) = (self.observe.as_ref(), descriptor.as_ref()) {
            if descriptor.observe_target.is_some() {
                let _ = observe
                    .index
                    .replace_from_descriptor(
                        &lineage.delegate_subject,
                        &d.assignee_bot_id,
                        &d.session_id,
                        descriptor,
                        d.now_ms,
                    );
            }
        }

        // ---- Bounded proof in the assignee's config-decided purpose. ----
        let subject_ref = CallerSubjectRef::new(
            CallerSubjectKind::Human,
            lineage.identity_subject_type,
            lineage.identity_subject.clone(),
            lineage.expires_at_ms,
        )
        .map_err(|_| Error::IssueFailed)?;
        let purpose = match policy {
            bcs_service_api::port::OrchestrationAssigneePolicy::Execution => {
                CallerExecutionPurpose::Execution
            }
            bcs_service_api::port::OrchestrationAssigneePolicy::DelegationOnly => {
                CallerExecutionPurpose::DelegationOnly
            }
        };
        let source = match parent_execution_token.as_ref() {
            Some(token) => IssueCallerExecutionSource::ParentExecutionTokenRef {
                token,
                parent_run_id: &d.workflow_run_id,
                parent_attempt: lineage.attempt,
            },
            None => IssueCallerExecutionSource::HumanOriginHandle,
        };
        let proof: CallerExecutionToken = match self
            .identity
            .issue_execution(bcs_caller_identity_api::IssueCallerExecution {
                staff_no: bcs_service_api::port::CallerOpaqueRef::new(lineage.delegate_subject.clone()),
                agent_code: self.target_agent_code(&d.assignee_bot_id).await,
                // A DelegationOnly Owner hop has no ensured Caller container:
                // no sandbox identity, no binding — subject from lineage only.
                sandbox_container_id: descriptor.as_ref().and_then(|d| d.sandbox_container_id.clone()),
                subject: subject_ref,
                run: bcs_caller_identity_api::CallerRunBinding {
                    run_id: d.node_run_id.clone(),
                    root_run_id: lineage.root_run_id.clone(),
                    parent_run_id: Some(d.workflow_run_id.clone()),
                    message_turn_id: Some(lineage.message_turn_id.clone()),
                    attempt: d.node_attempt.max(0) as u32,
                },
                scope: bcs_caller_identity_api::CallerProofScope {
                    env: d.environment.clone(),
                    target_bot_id: d.assignee_bot_id.clone(),
                    group_id: Some(d.group_id.clone()),
                    session_id: d.session_id.clone(),
                },
                delegation: bcs_caller_identity_api::CallerDelegationBounds { depth: lineage.delegation_depth + 1, max_depth: 2 },
                purpose,
                not_after_ms: effective_deadline,
                isolation: descriptor.as_ref().and_then(|descriptor| {
                    descriptor.binding_ref.as_ref().map(|binding| {
                        bcs_caller_identity_api::CallerIsolationConstraint {
                            binding_ref: binding.expose().to_string(),
                            generation: descriptor.runtime_generation,
                        }
                    })
                }),
                source,
                now_ms: d.now_ms,
            })
            .await
        {
            Ok(token) => token,
            Err(_) => return Err(Error::IssueFailed),
        };
        if proof.expires_at_ms() <= d.now_ms {
            return Err(Error::IssueFailed);
        }

        // ---- Canonical digest over the node facts verbatim (no managed
        // row owner exists here — the workflow's own claim/checkpoint
        // lifecycle retains the durability discipline). ----
        let request_digest = canonical_request_digest(
            &d.environment,
            &d.provider_id,
            &d.provider_bot_ref,
            &d.assignee_bot_id,
            &d.session_id,
            &d.node_run_id,
            // The orchestration dispatch id mirrors the node's own wire
            // request identity (node:node_id:attempt).
            &format!("node:{}:{}:{}", d.node_id, d.node_attempt, d.node_run_id),
            Some(&d.group_id),
            &lineage.message_turn_id,
            &d.caller_task,
            d.node_attempt as u32,
            d.control_revision,
            effective_deadline,
        );

        let command = bcs_service_api::port::SendCallerExecutionCommand {
            association: bcs_service_api::port::CallerDispatchAssociation {
                environment: d.environment.clone(),
                target_bot_id: d.assignee_bot_id.clone(),
                provider_id: d.provider_id.clone(),
                provider_bot_ref: d.provider_bot_ref.clone(),
                session_id: d.session_id.clone(),
                run_id: d.node_run_id.clone(),
                attempt: d.node_attempt as u32,
                dispatch_id: format!("node:{}:{}:{}", d.node_id, d.node_attempt, d.node_run_id),
            },
            group_id: Some(d.group_id.clone()),
            text: d.caller_task.clone(),
            caller_task: d.caller_task.clone(),
            message_turn_id: lineage.message_turn_id.clone(),
            root_run_id: lineage.root_run_id.clone(),
            caller_subject_ref: bcs_service_api::port::CallerOpaqueRef::new(
                lineage.delegate_subject.clone(),
            ),
            subject_kind: "human".to_string(),
            caller_context_expires_at_ms: lineage.expires_at_ms,
            execution_token: bcs_service_api::port::CallerOpaqueRef::new(
                proof.expose_token().to_string(),
            ),
            execution_token_expires_at_ms: proof.expires_at_ms(),
            proof_format: match proof.format() {
                CallerProofFormat::Jwt => "jwt".to_string(),
                CallerProofFormat::Opaque => "opaque".to_string(),
            },
            proof_type: self.identity.proof_type().to_string(),
            control_revision: d.control_revision,
            not_after_ms: effective_deadline,
            // The persisted node frame's trusted ordinary sender — the
            // envelope `from` zone's source, never the protected subject.
            channel_actor_id: d.channel_actor_id.clone(),
            channel_actor_name: d.channel_actor_name.clone(),
            request_digest,
            history: None,
            cross_group_lineage: None,
            callback_timeout_ms: 30_000,
            now_ms: d.now_ms,
        };

        if !self.delivery_term_is_current(at_term) { return Err(Error::LineageUnavailable); }
        let ack = match self
            .sender
            .send_caller_execution(target, service_credential, command)
            .await
        {
            Ok(ack) => ack,
            Err(CallerSendError::NotSent) => return Err(Error::PreparationFailed),
            // Ambiguity mirrors the roots: a possibly-started send refuses
            // closed here instead of guessing — node state converges via
            // the runtime's own send-marker discipline.
            Err(_) => {
                return Ok(CallerDispatchAck::Unknown);
            }
        };

        // ---- §8.5 node-run lineage: the assignee's run registers at
        // instance depth+1 (embedded A2A keeps incrementing from there). ----
        if let (CallerDispatchAck::Accepted { .. }, Some(index)) =
            (&ack, self.root_lineage.as_ref())
        {
            let node_execution = matches!(
                policy,
                bcs_service_api::port::OrchestrationAssigneePolicy::Execution
            );
            let node_lineage = RootLineage {
                root_run_id: lineage.root_run_id.clone(),
                environment: d.environment.clone(),
                delegation_depth: lineage.delegation_depth.saturating_add(1),
                attempt: d.node_attempt.max(0) as u32,
                message_turn_id: lineage.message_turn_id.clone(),
                group_id: d.group_id.clone(),
                session_id: d.session_id.clone(),
                history_cutoff_seq: lineage.history_cutoff_seq,
                expires_at_ms: effective_deadline,
                identity_subject: lineage.identity_subject.clone(),
                identity_subject_type: lineage.identity_subject_type,
                delegate_subject: lineage.delegate_subject.clone(),
                parent_execution: node_execution,
            };
            index.register_in_term(self.delivery_term.as_deref(), at_term, || {
            if node_execution {
                index.register_parent_execution(
                    &d.node_run_id,
                    node_lineage,
                    &d.assignee_bot_id,
                    proof,
                    d.now_ms,
                );
            } else {
                index.register(&d.node_run_id, node_lineage, d.now_ms);
            }
            });
        }
        Ok(ack)
    }
}

// The digest helper lives on the parent module; re-expose for this file.
use super::canonical_request_digest;

// Silence unused import lint for Arc (kept for API symmetry with the
// sibling modules).
#[allow(dead_code)]
fn _unused(_: Option<Arc<RootLineage>>) {}

// ---------------------------------------------------------------------------
// §15.83 S2: the instance-association registrar — the pipeline owns the
// runtime-only RootLineageIndex and registers the workflow instance's
// delegation-only association exactly like the §15.81 owner association:
// depth 0, the instance run as its own root, every node dispatch derived
// under it (§8.5). Idempotent per workflow run id.
// ---------------------------------------------------------------------------
#[async_trait]
impl bcs_service_api::port::OrchestrationInstanceRegistrar for CallerDispatchPipeline {
    async fn register_instance(
        &self,
        association: bcs_service_api::port::OrchestrationInstanceAssociation,
    ) -> Result<bool, bcs_service_api::port::OrchestrationDispatchError> {
        use bcs_service_api::port::OrchestrationDispatchError as Error;
        let at_term = self.delivery_term.as_ref().map(|v| v.load(std::sync::atomic::Ordering::SeqCst));
        let Some(index) = self.root_lineage.as_ref() else {
            // No lineage registry in the composition: the instance cannot
            // be caller-aware — refuse closed, the run proceeds Bot-only.
            return Err(Error::LineageUnavailable);
        };
        if association.workflow_run_id.trim().is_empty()
            || association.environment.trim().is_empty()
            || association.group_id.trim().is_empty()
            || association.session_id.trim().is_empty()
            || association.message_turn_id.trim().is_empty()
            || association.ingress.is_expired(association.now_ms)
            || association.ingress.env() != association.environment
            || association.now_ms <= 0
            || association.expires_at_ms <= association.now_ms
        {
            return Err(Error::InvalidInput);
        }
        let established = self.identity.establish_subject(bcs_caller_identity_api::EstablishCallerSubject {
            ingress: association.ingress.clone(), now_ms: association.now_ms,
        }).await.map_err(|_| Error::IssueFailed)?;
        let registered = index.register_in_term(self.delivery_term.as_deref(), at_term, || index.register(
            &association.workflow_run_id,
            RootLineage {
                root_run_id: association.workflow_run_id.clone(),
                environment: association.environment.clone(),
                delegation_depth: 0,
                attempt: 1,
                message_turn_id: association.message_turn_id.clone(),
                group_id: association.group_id.clone(),
                session_id: association.session_id.clone(),
                history_cutoff_seq: association.history_cutoff_seq,
                expires_at_ms: association.expires_at_ms.min(established.expires_at_ms),
                identity_subject: established.subject_ref.expose_ref().to_string(),
                identity_subject_type: established.subject_ref.ref_type(),
                delegate_subject: association.ingress.actor().staff_no.clone(),
                parent_execution: false,
            },
            association.now_ms,
        )).unwrap_or(false);
        if !registered {
            tracing::warn!(
                run_id = %association.workflow_run_id,
                "orchestration instance association refused (index capacity); the run proceeds caller-closed"
            );
        }
        Ok(registered)
    }
}
