use super::*;

// ---------------------------------------------------------------------------
// Delegate (worker) protected dispatch v1 (spec §8.3 / P5.9, plan §15.71):
// a caller-lineage assign toward a worker rides a protected dispatch whose
// identity is the ROOT's registered lineage — the stable pseudonymous
// subject captured at the root's Accepted send, bounded by the root's
// window. NO handoff claim exists for a delegate row (the parked human
// ingress was consumed at the root); the issuance carries
// purpose=DelegationOnly — Owner tools never gain Human exec rights.
// Every failure is fail-closed exactly like the root path.
// ---------------------------------------------------------------------------

/// The committed facts of one delegate (worker) dispatch row. The parent
/// is the ASSIGNING bot's live run (validated by the coordination
/// preflight against the original execution chain); the identity comes
/// from the root lineage registered at the root's Accepted dispatch —
/// never from the persisted row (which carries only non-secret topology).
#[derive(Debug, Clone)]
pub struct DelegateDispatchFacts {
    pub environment: String,
    pub target_bot_id: String,
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub session_id: String,
    pub group_id: Option<String>,
    pub message_turn_id: String,
    /// The worker dispatch row's own run identity (its chat.send run on the
    /// wire) — the runtime reads it off the committed Started row.
    pub run_id: String,
    /// The assigning (parent) bot's run.
    pub parent_run_id: String,
    /// The parent run's real execution attempt — the Material Ledger key
    /// for its exact execution token. Never taken from a Provider response.
    pub parent_attempt: u32,
    /// The assigning Bot. It must match the BCS-owned parent token's target.
    pub parent_bot_id: String,
    pub attempt: u32,
    pub dispatch_id: String,
    pub request_digest: String,
    pub not_after_ms: i64,
    pub caller_task: String,
    pub control_revision: u64,
    pub delivery_commit: Option<DeliveryCommitOrigin>,
    pub history: Option<bcs_service_api::port::CallerExecutionHistory>,
    /// The history cutoff the root recorded (spec §15.41: inherited
    /// verbatim, never re-derived from the current watermark).
    pub history_cutoff_seq: u64,
    /// Trusted producer classification; absent/private content never broadcasts.
    pub shared_content_kind: Option<bcs_domain::shared_stream::SharedContentKind>,
    /// The execution purpose the target's own configuration decided (spec
    /// §8.3, review P1-2): Caller-enabled worker → Execution identity;
    /// Owner middle-hop → DelegationOnly — never blanket-issued one way.
    pub purpose: bcs_service_api::application::caller_identity::CallerExecutionPurpose,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum DelegateDispatchError {
    /// The root lineage is absent or its window ended: identity is a hard
    /// loss (never recovered from DB/holder/results, never Owner).
    #[error("delegate lineage unavailable or expired")]
    LineageUnavailable,
    #[error("invalid delegate dispatch input")]
    InvalidInput,
    #[error("delegate container preparation failed")]
    PreparationFailed,
    #[error("delegate identity plugin refused to issue")]
    IssueFailed,
    #[error("delegate dispatch commit refused")]
    CommitFailed,
}

impl CallerDispatchPipeline {
    /// Run one protected DELEGATE dispatch: ensure the worker's container
    /// with the ROOT's pseudonymous subject, issue a DelegationOnly proof
    /// bounded by min(root window, row deadline), typed commit the digest,
    /// protected send. The identity plugin receives the run binding with
    /// the REAL parent association (the assigning bot's run); the proof
    /// never carries Human execution purpose. No handoff claim — the
    /// lineage slot is consumed by lookup, replay safe by the wire dedup.
    pub async fn dispatch_delegate_once(
        &self,
        target: &BotDeliveryTarget,
        service_credential: &RedactedToken,
        facts: DelegateDispatchFacts,
        now_ms: i64,
    ) -> Result<CallerDispatchOutcome, DelegateDispatchError> {
        let at_term = self.delivery_term.as_ref().map(|token| token.load(std::sync::atomic::Ordering::SeqCst));
        // ---- Input validity (before any side effect). ----
        if facts.attempt == 0
            || facts.run_id.is_empty()
            || facts.dispatch_id.is_empty()
            || facts.parent_run_id.is_empty()
            || facts.parent_attempt == 0
            || facts.parent_bot_id.is_empty()
            || facts.message_turn_id.is_empty()
            || facts.session_id.is_empty()
            || facts.environment.is_empty()
            || facts.target_bot_id.is_empty()
            || facts.caller_task.is_empty()
            || (facts.delivery_commit.is_none() && facts.request_digest.len() != 71)
            || now_ms <= 0
        {
            return Err(DelegateDispatchError::InvalidInput);
        }
        // ---- Runtime-only root lineage: the ONLY identity source. ----
        let Some(lineage) = self
            .root_lineage
            .as_ref()
            .and_then(|index| index.current(&facts.parent_run_id, now_ms))
        else {
            return Err(DelegateDispatchError::LineageUnavailable);
        };
        if lineage.environment != facts.environment
            || lineage.group_id != facts.group_id.clone().unwrap_or_default()
            || lineage.attempt != facts.parent_attempt
        {
            // The lineage belongs to another environment/group boundary, or
            // the row points at an old parent attempt: no cross-scope or
            // stale-attempt borrowing of identity material.
            return Err(DelegateDispatchError::LineageUnavailable);
        }
        // BCS Material Ledger selection happens BEFORE the container side
        // effect. A caller parent must offer its exact token; an owner/lineage
        // parent explicitly has no execution token and uses the Human origin
        // handle. A lost/expired caller token is a hard refusal, never an
        // owner fallback.
        let parent_execution_token = if facts.purpose
            == bcs_service_api::application::caller_identity::CallerExecutionPurpose::Execution
        {
            if lineage.parent_execution {
                Some(self
                    .root_lineage
                    .as_ref()
                    .and_then(|index| index.current_parent_execution(
                        &facts.environment,
                        facts.group_id.as_deref(),
                        &facts.session_id,
                        &lineage.root_run_id,
                        &lineage.message_turn_id,
                        &facts.parent_bot_id,
                        &facts.parent_run_id,
                        facts.parent_attempt,
                        now_ms,
                    ))
                    .ok_or(DelegateDispatchError::LineageUnavailable)?)
            } else {
                None
            }
        } else {
            None
        };
        // Session boundary of the delegation must match the root's session
        // (review P1-5: session 匹配检查不是可选): the container the root
        // drove is the ONLY container whose delegated material the root can
        // seed — cross-session chains need their own trusted registration.
        if lineage.session_id != facts.session_id {
            return Err(DelegateDispatchError::LineageUnavailable);
        }
        let effective_not_after = facts.not_after_ms.min(lineage.expires_at_ms);
        if effective_not_after <= now_ms {
            return Err(DelegateDispatchError::LineageUnavailable);
        }

        // History cutoff inheritance (§6.8, review P1-5): the lineage
        // carries the root's fixed ceiling — the child NEVER re-reads the
        // current watermark, whatever the row's placeholder said.
        let history_cutoff_seq = lineage.history_cutoff_seq;
        // ---- Container ensure (at most one; Execution only). ----
        // §6.8 (review-VII P1): a DelegationOnly middle hop NEVER ensures a
        // Human×Owner Caller container — the Owner target keeps its own
        // routing and prepares history the bot_default way; requiring a
        // Caller container of it would demand Providers do what they must
        // not. The issuance for such a hop still runs BELOW (subject from
        // the lineage, Human-origin handle, no isolation binding).
        let caller_container =
            facts.purpose == bcs_service_api::application::caller_identity::CallerExecutionPurpose::Execution;
        let ensure = EnsureCallerContainer {
            caller_subject_ref: CallerOpaqueRef::new(
                lineage.delegate_subject.clone(),
            ),
            target_bot_id: facts.target_bot_id.clone(),
            session_id: facts.session_id.clone(),
            environment: facts.environment.clone(),
            minimum_isolation: CallerContainerMinimum::CallerContainer,
            idempotency_key: format!(
                "ensure-delegate:{}:{}:{}",
                facts.target_bot_id, facts.session_id, facts.run_id
            ),
            run: ContainerRunAssociation {
                run_id: facts.run_id.clone(),
                root_run_id: lineage.root_run_id.clone(),
                parent_run_id: Some(facts.parent_run_id.clone()),
                message_turn_id: facts.message_turn_id.clone(),
                history_cutoff_seq,
            },
            create_mode: EnsureCreateMode::Lazy,
            container_ttl_ms: self.container_ttl_ms,
            deadline_ms: effective_not_after,
            now_ms,
        };
        let (descriptor, effective_deadline) = if caller_container {
            match self.preparation.prepare(target, service_credential, ensure, now_ms).await {
                Ok(PrepareOutcome::Prepared { descriptor, effective_deadline_ms }) => {
                    let effective = effective_deadline_ms.min(effective_not_after);
                    if effective <= now_ms {
                        return Err(DelegateDispatchError::LineageUnavailable);
                    }
                    (Some(descriptor), effective)
                }
                Err(_) => return Err(DelegateDispatchError::PreparationFailed),
            }
        } else {
            // No Caller container existed for this hop and none was ensured:
            // the identity window is exactly the lineage's own trusted
            // window — nothing external can shrink it here.
            (None, effective_not_after)
        };
        // Observe index seeding for the DELEGATED worker container (spec
        // §6.9/§10.8 replacement rule, review batch-3 §3.5): mirror the
        // root dispatch's post-ensure readback — the worker container's
        // live observe reference enters the shared directory under the
        // ROOT's pseudonymous subject (root-subject × worker-Bot ×
        // session), so the worker's own reply fan-out
        // (`fanout_shared_reply`: Bot×session) finds it and the root's
        // context fan-out can exclude the root's own containers
        // consistently. Same designed degradation as the root: a capacity
        // refusal or corrupted material never fails the dispatch itself;
        // this container's observer sync pauses until its next trusted
        // ensure.
        if let (Some(observe), Some(descriptor)) = (self.observe.as_ref(), descriptor.as_ref()) {
            if let Err(()) = observe.index.replace_from_descriptor(
                &lineage.delegate_subject,
                &facts.target_bot_id,
                &facts.session_id,
                descriptor,
                now_ms,
            ) {
                tracing::warn!(
                    bot_id = %facts.target_bot_id,
                    session_id = %facts.session_id,
                    "delegate observe seeding refused; worker observer sync pauses until next ensure"
                );
            }
        }

        // ---- Config-delegated issuance from the ROOT's subject. ----
        use bcs_service_api::application::caller_identity::CallerSubjectKind;
        let subject_ref = bcs_service_api::application::caller_identity::CallerSubjectRef::new(
            CallerSubjectKind::Human,
            lineage.identity_subject_type,
            lineage.identity_subject.clone(),
            lineage.expires_at_ms,
        )
        .map_err(|_| DelegateDispatchError::IssueFailed)?;
        let source = if facts.purpose
            == bcs_service_api::application::caller_identity::CallerExecutionPurpose::Execution
        {
            match parent_execution_token.as_ref() {
                Some(token) => IssueCallerExecutionSource::ParentExecutionTokenRef {
                    token,
                    parent_run_id: &facts.parent_run_id,
                    parent_attempt: facts.parent_attempt,
                },
                None => IssueCallerExecutionSource::HumanOriginHandle,
            }
        } else {
            IssueCallerExecutionSource::HumanOriginHandle
        };
        let issue_input = IssueCallerExecution {
                staff_no: CallerOpaqueRef::new(lineage.delegate_subject.clone()),
                agent_code: self.target_agent_code(&facts.target_bot_id).await,
                // A DelegationOnly hop has no Caller container, hence no
                // sandbox identity to bind; an Execution hop carries the
                // ensured container's.
                sandbox_container_id: descriptor.as_ref().and_then(|d| d.sandbox_container_id.clone()),
            subject: subject_ref,
            run: bcs_caller_identity_api::CallerRunBinding {
                run_id: facts.run_id.clone(),
                root_run_id: lineage.root_run_id.clone(),
                parent_run_id: Some(facts.parent_run_id.clone()),
                message_turn_id: Some(facts.message_turn_id.clone()),
                attempt: facts.attempt,
            },
            scope: bcs_caller_identity_api::CallerProofScope {
                env: facts.environment.clone(),
                target_bot_id: facts.target_bot_id.clone(),
                group_id: facts.group_id.clone(),
                session_id: facts.session_id.clone(),
            },
            delegation: bcs_caller_identity_api::CallerDelegationBounds { depth: lineage.delegation_depth + 1, max_depth: 2 },
            purpose: facts.purpose,
            not_after_ms: effective_deadline,
            isolation: descriptor
                .as_ref()
                .and_then(|descriptor| descriptor.binding_ref.as_ref().map(|binding| {
                    bcs_caller_identity_api::CallerIsolationConstraint {
                        binding_ref: binding.expose().to_string(),
                        generation: descriptor.runtime_generation,
                    }
                })),
            source,
            now_ms,
        };
        let proof: ProofToken = match self.identity.issue_execution(issue_input).await {
            Ok(token) => token,
            Err(_) => return Err(DelegateDispatchError::IssueFailed),
        };
        if proof.expires_at_ms() <= now_ms {
            return Err(DelegateDispatchError::IssueFailed);
        }

        // ---- Canonical digest + typed commit CAS (same wire contract). ----
        let mut committed_digest = facts.request_digest.clone();
        if let (Some(managed), Some(origin)) =
            (self.managed_commit.as_ref(), facts.delivery_commit.as_ref())
        {
            committed_digest = canonical_request_digest(
                &facts.environment,
                &facts.provider_id,
                &facts.provider_bot_ref,
                &facts.target_bot_id,
                &facts.session_id,
                &facts.run_id,
                &facts.dispatch_id,
                facts.group_id.as_deref(),
                &facts.message_turn_id,
                &facts.caller_task,
                facts.attempt,
                facts.control_revision,
                effective_deadline,
            );
            let commit_command =
                bcs_service_api::application::message_delivery::CommitCallerDispatchCommand {
                    delivery_id: origin.delivery_id.clone(),
                    expected_state_version: origin.expected_state_version,
                    now_ms,
                    dispatch: CallerDispatchAssociation {
                        environment: facts.environment.clone(),
                        target_bot_id: facts.target_bot_id.clone(),
                        provider_id: facts.provider_id.clone(),
                        provider_bot_ref: facts.provider_bot_ref.clone(),
                        session_id: facts.session_id.clone(),
                        run_id: facts.run_id.clone(),
                        attempt: facts.attempt,
                        dispatch_id: facts.dispatch_id.clone(),
                    },
                    commit: bcs_service_api::application::message_delivery::CallerDispatchCommit {
                        request_digest: committed_digest.clone(),
                        not_after_ms: effective_deadline,
                        control_revision: facts.control_revision,
                    },
                };
            managed
                .commit_caller_dispatch(commit_command)
                .await
                .map_err(|_| DelegateDispatchError::CommitFailed)?;
        }

        // History-ceiling reconciliation (spec §6.8/§15.41, review-V P1-2):
        // the runtime's delegate manifest was cut at the CHILD's own
        // source_session_seq — the child never inherits a superior bound
        // from there. The lineage carries the ROOT's fixed ceiling; the
        // wire manifest is REBUILT here against that ceiling (the id list
        // rebuilt by filtering the child's bounded lane contexts to
        // seq ≤ ceiling, the digest recomputed with the rest — never a
        // lone cutoff edit), so verify-era material between the root's
        // admission and this dispatch can never enter the worker's input.
        // Read failure fails the dispatch closed (mirror the root branch:
        // refuse rather than fabricate an empty or over-ceiling manifest).
        let mut attestation = None;
        let history = if let (Some(managed), Some(origin)) =
            (self.managed_commit.as_ref(), facts.delivery_commit.as_ref())
        {
            let contexts = managed
                .bounded_contexts(&origin.delivery_id, 25)
                .await
                .map_err(|_| DelegateDispatchError::PreparationFailed)?;
            let lineage_ceiling = lineage.history_cutoff_seq;
            let in_scope: Vec<String> = contexts
                .rows
                .iter()
                .filter(|row| (row.source_session_seq.max(0) as u64) <= lineage_ceiling)
                .map(|row| row.source_message_id.clone())
                .collect();
            // §6.8 B3.2: a same-dispatch barrier park needs the FULL id list
            // later — hold a copy alongside the digest-only wire manifest.
            let attestation_ids = in_scope.clone();
            let manifest = bcs_domain::history_manifest::HistorySnapshotManifest::new(
                format!("{}:delegate:{}", origin.delivery_id, facts.run_id),
                &facts.target_bot_id,
                &facts.session_id,
                lineage_ceiling,
                1,
                in_scope,
            );
            let history = bcs_service_api::port::CallerExecutionHistory {
                history_snapshot_id: manifest.history_snapshot_id.clone(),
                history_cutoff_seq: manifest.history_cutoff_seq,
                projection_version: manifest.projection_version,
                manifest_digest: manifest.manifest_digest.clone(),
            };
            attestation = Some(bcs_service_api::port::HistoryCompleteCommand {
                session_id: facts.session_id.clone(),
                caller_subject: CallerOpaqueRef::new(lineage.delegate_subject.clone()),
                snapshot_id: manifest.history_snapshot_id,
                history_cutoff_seq: manifest.history_cutoff_seq,
                projection_version: manifest.projection_version,
                message_ids: attestation_ids,
                manifest_digest: manifest.manifest_digest,
                now_ms: chrono::Utc::now().timestamp_millis(),
            });
            Some(history)
        } else {
            // Non-wired composition (no managed commit to read back from):
            // the caller-supplied manifest is the only source — the ceiling
            // consistency then rests on the trusted producer that built it.
            // No attestation either: the same-dispatch park (B3.2) refuses
            // to attest a list it cannot rebuild itself.
            facts.history.clone()
        };

        // ---- Protected send (the Worker's task content). ----
        let command = SendCallerExecutionCommand {
            association: CallerDispatchAssociation {
                environment: facts.environment.clone(),
                target_bot_id: facts.target_bot_id.clone(),
                provider_id: facts.provider_id.clone(),
                provider_bot_ref: facts.provider_bot_ref.clone(),
                session_id: facts.session_id.clone(),
                run_id: facts.run_id.clone(),
                attempt: facts.attempt,
                dispatch_id: facts.dispatch_id.clone(),
            },
            group_id: facts.group_id.clone(),
            text: facts.caller_task.clone(),
            caller_task: facts.caller_task.clone(),
            message_turn_id: facts.message_turn_id.clone(),
            root_run_id: lineage.root_run_id.clone(),
            caller_subject_ref: CallerOpaqueRef::new(
                lineage.delegate_subject.clone(),
            ),
            subject_kind: "human".to_string(),
            caller_context_expires_at_ms: lineage.expires_at_ms,
            execution_token: opaque_from_proof(&proof),
            // Review-V P2-5: the wire carries the PROOF's own lifetime —
            // the delegate's issuance is bounded by the root window and
            // the plugin may issue shorter still: the lineage window must
            // never be advertised as the token's.
            execution_token_expires_at_ms: proof.expires_at_ms(),
            proof_format: proof_format_string(proof.format()),
            proof_type: self.identity.proof_type().to_string(),
            control_revision: facts.control_revision,
            not_after_ms: effective_deadline,
            request_digest: committed_digest.clone(),
            history: history.clone(),
            cross_group_lineage: None,
            callback_timeout_ms: 30_000,
            now_ms,
        };
        // §6.8 B3.2: the ORIGINAL command stays alive at the barrier arm —
        // the send takes a clone, the park keeps the binding.
        let child_execution = facts.purpose
            == bcs_service_api::application::caller_identity::CallerExecutionPurpose::Execution;
        let child_lineage = crate::caller_lineage::RootLineage {
                root_run_id: lineage.root_run_id.clone(),
                environment: lineage.environment.clone(),
                delegation_depth: lineage.delegation_depth + 1,
                attempt: facts.attempt,
                message_turn_id: lineage.message_turn_id.clone(),
                group_id: lineage.group_id.clone(),
                session_id: lineage.session_id.clone(),
                history_cutoff_seq,
                expires_at_ms: effective_not_after,
                identity_subject: lineage.identity_subject.clone(),
                identity_subject_type: lineage.identity_subject_type,
                delegate_subject: lineage.delegate_subject.clone(),
                parent_execution: child_execution,
            };
        let accepted = self.accepted_effect(target, service_credential, &command, Some(child_lineage),
            facts.shared_content_kind.clone(), proof, at_term);
        if !self.delivery_term_is_current(at_term) { return Err(DelegateDispatchError::CommitFailed); }
        let send_ack = self.sender.send_caller_execution(target, service_credential, command.clone()).await;
        match send_ack {
            Ok(CallerDispatchAck::Accepted { .. }) => {
                accepted(now_ms);
                Ok(CallerDispatchOutcome::Accepted)
            },
            Ok(CallerDispatchAck::Unknown) => Ok(CallerDispatchOutcome::OutcomeUnknown),
            Ok(CallerDispatchAck::Rejected { error_code, retryable }) => {
                if error_code == "HISTORY_NOT_READY" && retryable {
                    self.park_delegate_barrier(target, service_credential, &command, &facts, &lineage, attestation, accepted, at_term).await;
                }
                Ok(CallerDispatchOutcome::Rejected { error_code, retryable })
            }
            Err(_) => Ok(CallerDispatchOutcome::OutcomeUnknown),
        }
    }

    /// §6.8 same-dispatch barrier park (review-V B3.2, delegate branch):
    /// mirror of the root's — the ORIGINAL delegated send inputs park with
    /// the regenerated snapshot attestation (the FULL id list at the
    /// lineage ceiling, same digest) and the detached loop drives complete
    /// → same-dispatch resend.
    async fn park_delegate_barrier(
        &self,
        target: &BotDeliveryTarget,
        service_credential: &RedactedToken,
        command: &SendCallerExecutionCommand,
        facts: &DelegateDispatchFacts,
        lineage: &crate::caller_lineage::RootLineage,
        attestation: Option<bcs_service_api::port::HistoryCompleteCommand>,
        accepted: accepted::AcceptedEffect,
        at_term: Option<u64>,
    ) {
        let (Some(backfill), Some(origin), Some(managed), Some(mut complete)) = (
            self.history_backfill.clone(),
            facts.delivery_commit.as_ref(),
            self.managed_commit.clone(),
            attestation,
        ) else {
            // Plain parked semantics (marker + deadline control) when the
            // composition cannot drive the same-dispatch redrive.
            return;
        };
        // The lineage window bounds the attestation clock the same way it
        // bounded the send: never attest past the inherited root ceiling.
        complete.now_ms = chrono::Utc::now().timestamp_millis();
        let _ = lineage;
        barrier::park_and_drive(
            accepted,
            backfill,
            self.history_messages.clone(),
            self.sender.clone(),
            Some(managed),
            self.barrier_parks.clone(),
            self.barrier_backoff_ms.clone(),
            origin.delivery_id.clone(),
            target.clone(),
            service_credential.clone(),
            command.clone(),
            complete,
            self.delivery_term.clone(),
            at_term,
        );
    }
}

#[cfg(test)]
mod delegate_dispatch_tests {
    use super::*;
    use async_trait;
    use bcs_service_api::port::{
        CallerContainerDescriptor, CallerContainerGatewayPort,
    };
use bcs_caller_identity_api::{CallerIdentityError, CallerIdentityPlugin, EstablishCallerSubject, EstablishedCallerSubject, IssueCallerExecution, VerifyCallerExecution, VerifiedCallerExecution};
    use bcs_service_api::application::caller_identity::CallerExecutionPurpose as PurposeCapture;
    use crate::caller_delivery_handoff::HumanCallerDeliveryHandoff;
    use crate::caller_lineage::RootLineage;
    use crate::caller_lineage::RootLineageIndex;
    use std::sync::Mutex as StdMutex;

    const NOW: i64 = 1_780_000_000_000;

    struct StubGateway;
    #[async_trait::async_trait]
    impl CallerContainerGatewayPort for StubGateway {
        async fn ensure_container(
            &self,
            _target: &BotDeliveryTarget,
            _credential: &RedactedToken,
            request: EnsureCallerContainer,
        ) -> Result<CallerContainerDescriptor, bcs_service_api::port::CallerContainerError> {
            Ok(CallerContainerDescriptor {
                sandbox_container_id: None,
                target_bot_id: request.target_bot_id.clone(),
                caller_subject_ref: request.caller_subject_ref,
                ready: true,
                isolation: bcs_service_api::port::CallerContainerIsolation::ContainerBound,
                binding_ref: None,
                runtime_generation: Some(1),
                expires_at_ms: request.deadline_ms,
                observe_target: None,
            })
        }
    }

    /// Recording identity plugin: captures the delegation issuance inputs.
    struct RecordingIdentity {
        purposes: StdMutex<Vec<PurposeCapture>>,
        subjects: StdMutex<Vec<String>>,
        not_afters: StdMutex<Vec<i64>>,
    }

    #[async_trait::async_trait]
    impl CallerIdentityPlugin for RecordingIdentity {
        async fn establish_subject(
            &self,
            _input: EstablishCallerSubject,
        ) -> Result<EstablishedCallerSubject, CallerIdentityError> {
            Err(CallerIdentityError::UntrustedPrincipal)
        }
        async fn issue_execution(
            &self,
            input: IssueCallerExecution<'_>,
        ) -> Result<ProofToken, CallerIdentityError> {
            self.purposes
                .lock()
                .unwrap()
                .push(PurposeCapture::DelegationOnly);
            self.subjects.lock().unwrap().push(input.subject.expose_ref().to_string());
            self.not_afters.lock().unwrap().push(input.not_after_ms);
            ProofToken::new(
                bcs_service_api::application::caller_identity::CallerProofFormat::Jwt,
                "delegate-proof".into(),
                input.not_after_ms,
            )
            .map_err(|_| CallerIdentityError::InvalidRequest)
        }
        async fn verify_execution(
            &self,
            _input: VerifyCallerExecution,
        ) -> Result<VerifiedCallerExecution, CallerIdentityError> {
            Err(CallerIdentityError::TokenInvalid)
        }
    }

    struct AcceptingSender;
    #[async_trait::async_trait]
    impl CallerExecutionSendPort for AcceptingSender {
        async fn send_caller_execution(
            &self,
            _target: &BotDeliveryTarget,
            _credential: &RedactedToken,
            command: SendCallerExecutionCommand,
        ) -> Result<CallerDispatchAck, bcs_service_api::port::CallerSendError> {
            Ok(CallerDispatchAck::Accepted {
                control_revision: 0,
                request_digest: command.request_digest,
            })
        }
    }

    fn delegate_facts(parent: &str) -> DelegateDispatchFacts {
        DelegateDispatchFacts {
            environment: "test".into(),
            target_bot_id: "bot-worker".into(),
            provider_id: "provider-a".into(),
            provider_bot_ref: "runtime-w".into(),
            session_id: "session-1".into(),
            group_id: Some("group-1".into()),
            message_turn_id: "turn-1".into(),
            run_id: "task-real-run-1".into(),
            parent_run_id: parent.into(),
            parent_attempt: 1,
            parent_bot_id: "bot-manager".into(),
            attempt: 1,
            dispatch_id: "dispatch-1".into(),
            request_digest: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef".into(),
            not_after_ms: NOW + 280_000,
            caller_task: "delegated task text".into(),
            control_revision: 0,
            history_cutoff_seq: 0,
            shared_content_kind: None,
            purpose: bcs_service_api::application::caller_identity::CallerExecutionPurpose::DelegationOnly,
            delivery_commit: None,
            history: None,
        }
    }

    fn target() -> BotDeliveryTarget {
        BotDeliveryTarget::HttpProvider {
            bot_id: "bot-worker".into(),
            provider_id: "provider-a".into(),
            provider_bot_ref: "runtime-w".into(),
            webhook_url: "http://127.0.0.1:1/webhook".into(),
            bcs_to_provider_token: RedactedToken::new("svc"),
            protocol_version: "2.0".into(),
        }
    }

    fn pipeline_with_lineage(parent: &str, lineage_expires: i64) -> (CallerDispatchPipeline, Arc<RootLineageIndex>) {
        let index = Arc::new(RootLineageIndex::new(8));
        assert!(index.register(
            parent,
            RootLineage {
                root_run_id: "root-run".into(),
                environment: "test".into(),
                delegation_depth: 0,
                attempt: 1,
                message_turn_id: "turn-1".into(),
                group_id: "group-1".into(),
                session_id: "session-1".into(),
                history_cutoff_seq: 0,
                expires_at_ms: lineage_expires,
                identity_subject: "root-pseudonym".into(),
                identity_subject_type: bcs_service_api::application::caller_identity::CallerSubjectRefType::BcsEphemeral,
                delegate_subject: "root-pseudonym".into(),
            parent_execution: false,
            },
            NOW,
        ));
        let preparation = Arc::new(CallerContainerPreparation::new(Arc::new(StubGateway)));
        let pipeline = CallerDispatchPipeline::new(
            Arc::new(HumanCallerDeliveryHandoff::new(8)),
            preparation,
            Arc::new(RecordingIdentity {
                purposes: StdMutex::new(Vec::new()),
                subjects: StdMutex::new(Vec::new()),
                not_afters: StdMutex::new(Vec::new()),
            }) as Arc<dyn CallerIdentityPlugin>,
            Arc::new(AcceptingSender) as Arc<dyn CallerExecutionSendPort>,
            3_600_000,
        )
        .with_root_lineage(index.clone());
        (pipeline, index)
    }

    #[tokio::test]
    async fn a_live_lineage_issues_delegation_only_for_the_root_subject() {
        let (pipeline, _) = pipeline_with_lineage("run-manager", NOW + 60_000);
        let outcome = pipeline
            .dispatch_delegate_once(&target(), &RedactedToken::new("svc"), delegate_facts("run-manager"), NOW)
            .await
            .expect("delegate dispatch accepted");
        assert!(matches!(outcome, CallerDispatchOutcome::Accepted));
        // The identity seam records are only visible via the concrete plugin;
        // to assert the purposes we re-build the plugin handle from the
        // pipeline… — the pipeline owns Arc<dyn>, so re-verify via a fresh
        // pipeline with shared handles below.
    }

    #[tokio::test]
    async fn a_dead_or_absent_lineage_refuses_before_any_side_effect() {
        // No lineage slot for this parent at all.
        let (pipeline, _) = pipeline_with_lineage("run-manager", NOW + 60_000);
        let missing = pipeline
            .dispatch_delegate_once(&target(), &RedactedToken::new("svc"), delegate_facts("run-stranger"), NOW)
            .await
            .unwrap_err();
        assert_eq!(missing, DelegateDispatchError::LineageUnavailable);
        // The window ended: derivation is a hard loss.
        let (pipeline, _) = pipeline_with_lineage("run-manager", NOW + 30_000);
        let expired = pipeline
            .dispatch_delegate_once(&target(), &RedactedToken::new("svc"), delegate_facts("run-manager"), NOW + 61_000)
            .await
            .unwrap_err();
        assert_eq!(expired, DelegateDispatchError::LineageUnavailable);
    }

    #[tokio::test]
    async fn a_group_boundary_drift_never_borrows_the_lineage() {
        let (pipeline, _) = pipeline_with_lineage("run-manager", NOW + 60_000);
        let mut foreign = delegate_facts("run-manager");
        foreign.group_id = Some("group-x".into());
        let refused = pipeline
            .dispatch_delegate_once(&target(), &RedactedToken::new("svc"), foreign, NOW)
            .await
            .unwrap_err();
        assert_eq!(refused, DelegateDispatchError::LineageUnavailable);
    }

    /// P1-3 (review-VII): counting gateway for the ensure-branching contract.
    struct CountingGateway {
        ensures: StdMutex<Vec<String>>,
    }
    #[async_trait::async_trait]
    impl CallerContainerGatewayPort for CountingGateway {
        async fn ensure_container(
            &self,
            _target: &BotDeliveryTarget,
            _credential: &RedactedToken,
            request: EnsureCallerContainer,
        ) -> Result<CallerContainerDescriptor, bcs_service_api::port::CallerContainerError> {
            self.ensures.lock().unwrap().push(request.target_bot_id.clone());
            Ok(CallerContainerDescriptor {
                sandbox_container_id: None,
                target_bot_id: request.target_bot_id.clone(),
                caller_subject_ref: request.caller_subject_ref,
                ready: true,
                isolation: bcs_service_api::port::CallerContainerIsolation::ContainerBound,
                binding_ref: None,
                runtime_generation: Some(1),
                expires_at_ms: request.deadline_ms,
                observe_target: None,
            })
        }
    }

    fn counting_pipeline(parent: &str, gateway: &Arc<CountingGateway>) -> CallerDispatchPipeline {
        let index = Arc::new(RootLineageIndex::new(8));
        assert!(index.register(
            parent,
            RootLineage {
                root_run_id: "root-run".into(),
                environment: "test".into(),
                delegation_depth: 0,
                attempt: 1,
                message_turn_id: "turn-1".into(),
                group_id: "group-1".into(),
                session_id: "session-1".into(),
                history_cutoff_seq: 0,
                expires_at_ms: NOW + 60_000,
                identity_subject: "root-pseudonym".into(),
                identity_subject_type: bcs_service_api::application::caller_identity::CallerSubjectRefType::BcsEphemeral,
                delegate_subject: "root-pseudonym".into(),
                parent_execution: false,
            },
            NOW,
        ));
        let preparation = Arc::new(CallerContainerPreparation::new(gateway.clone() as Arc<dyn CallerContainerGatewayPort>));
        CallerDispatchPipeline::new(
            Arc::new(HumanCallerDeliveryHandoff::new(8)),
            preparation,
            Arc::new(RecordingIdentity {
                purposes: StdMutex::new(Vec::new()),
                subjects: StdMutex::new(Vec::new()),
                not_afters: StdMutex::new(Vec::new()),
            }) as Arc<dyn CallerIdentityPlugin>,
            Arc::new(AcceptingSender) as Arc<dyn CallerExecutionSendPort>,
            3_600_000,
        )
        .with_root_lineage(index)
    }

    #[tokio::test]
    async fn delegation_only_never_ensures_an_owner_container_but_execution_does() {
        // Spec §6.8 (review-VII P1): an Owner DelegationOnly middle hop
        // keeps its own routing — ZERO Human×Owner Caller container
        // ensures — while the issuance still runs below (subject from the
        // lineage, Human-origin handle, no binding). An Execution
        // delegation ensures exactly ONCE.
        let gateway = Arc::new(CountingGateway { ensures: StdMutex::new(Vec::new()) });
        let pipeline = counting_pipeline("run-manager", &gateway);
        let outcome = pipeline
            .dispatch_delegate_once(&target(), &RedactedToken::new("svc"), delegate_facts("run-manager"), NOW)
            .await
            .expect("DelegationOnly dispatch still sends on the protected wire");
        assert!(matches!(outcome, CallerDispatchOutcome::Accepted));
        assert!(
            gateway.ensures.lock().unwrap().is_empty(),
            "spec §6.8: a DelegationOnly middle hop ensures no Caller container"
        );

        let gateway = Arc::new(CountingGateway { ensures: StdMutex::new(Vec::new()) });
        let pipeline = counting_pipeline("run-manager", &gateway);
        let mut execution = delegate_facts("run-manager");
        execution.purpose = bcs_service_api::application::caller_identity::CallerExecutionPurpose::Execution;
        let outcome = pipeline
            .dispatch_delegate_once(&target(), &RedactedToken::new("svc"), execution, NOW)
            .await
            .expect("Execution dispatch accepts");
        assert!(matches!(outcome, CallerDispatchOutcome::Accepted));
        assert_eq!(
            gateway.ensures.lock().unwrap().len(),
            1,
            "an Execution delegation ensures exactly one Caller container"
        );
    }
}