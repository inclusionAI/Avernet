//! Caller delegation uses the existing managed dispatch lifecycle.
use super::*;

impl DeliveryRuntime {
    /// P5.9 delegate (worker) dispatch: the StartSend commit + the
    /// lineage-bounded issuance/ensure/send mirroring the root's sequence,
    /// with the ROOT's registered lineage (runtime-only) as the identity
    /// source instead of a handoff claim. Facts are the row's committed
    /// ones; the pipeline owns lineage lookup, DelegationOnly issuance and
    /// the typed digest commit; convergence uses the same lifecycle events.
    #[allow(clippy::too_many_arguments)]
    pub(super) async fn attempt_caller_delegate_dispatch(
        &self,
        pipeline: &Arc<crate::caller_dispatch_pipeline::CallerDispatchPipeline>,
        context: &bcs_service_api::CallerDispatchContext,
        digest: String,
        caller_task: String,
        control_revision: u64,
        start: DeliveryTransitionCommand,
        delegate_parent: Option<String>,
    ) {
        let Some(parent_run) = delegate_parent else { return };
        let Some(started) = self.transition_ok(start.clone()).await else {
            return; // lost the CAS race — the row already converged
        };
        let Some(run_id) = started.run_id.clone() else {
            self.transition_ok(event(&started, Event::DefinitelyNotSent { retry: false })).await;
            return;
        };
        let Some(dispatch_id) = started.request_id.clone() else {
            self.transition_ok(event(&started, Event::DefinitelyNotSent { retry: false })).await;
            return;
        };
        let Some(not_after_ms) = started
            .run_deadline_at_ms
            .filter(|deadline| *deadline > now_ms())
        else {
            let mut failed = event(&started, Event::DefinitelyNotSent { retry: false });
            failed.request_id = started.request_id.clone();
            self.transition_ok(failed).await;
            return;
        };
        // P4 fixed history manifest on the DELEGATE wire too (review P1-5
        // 尾段): the worker's container needs the same explicit id-list
        // manifest the root send carries — same bounded-contexts read, same
        // cutoff (lineage overrides placeholder in the pipeline), same fail
        // closed on a read failure.
        let Some(parent_attempt) = started
            .semantic_projection_json
            .get("caller_delegation")
            .and_then(|value| value.get("parent_attempt"))
            .and_then(serde_json::Value::as_u64)
            .filter(|attempt| *attempt > 0 && *attempt <= u32::MAX as u64)
            .map(|attempt| attempt as u32)
        else {
            self.transition_ok(event(&started, Event::PreparationFailed)).await;
            return;
        };
        let Some(parent_bot_id) = started
            .semantic_projection_json
            .get("caller_delegation")
            .and_then(|value| value.get("parent_bot_id"))
            .and_then(serde_json::Value::as_str)
            .filter(|bot_id| !bot_id.trim().is_empty())
            .map(str::to_string)
        else {
            self.transition_ok(event(&started, Event::PreparationFailed)).await;
            return;
        };
        // Same trusted ordinary sender facts as the root branch (fail
        // closed): the delegate wire still needs the envelope's `from`, and
        // the protected subject never fills the ordinary zone.
        let Some((channel_actor_id, channel_actor_name)) = caller_channel_facts(&started) else {
            tracing::warn!(
                delivery_id = %started.delivery_id,
                bot_id = %started.target_bot_id,
                "CALLER_EXECUTION_UNAVAILABLE: delegate row carries no committed sender facts, staying closed"
            );
            self.transition_ok(event(&started, Event::PreparationFailed)).await;
            return;
        };
        let history_header = {
            let contexts = self.service.bounded_contexts(&started.delivery_id, 25).await;
            let Ok(contexts) = contexts else {
                self.transition_ok(event(&started, Event::PreparationFailed)).await;
                return;
            };
            let message_ids: Vec<String> = contexts
                .rows
                .iter()
                .map(|context| context.source_message_id.clone())
                .collect();
            let manifest = bcs_domain::history_manifest::HistorySnapshotManifest::new(
                format!("{}:delegate:{}", started.delivery_id, run_id),
                &started.target_bot_id,
                &started.session_id,
                started.source_session_seq as u64,
                1,
                message_ids,
            );
            Some(bcs_service_api::port::CallerExecutionHistory {
                history_snapshot_id: manifest.history_snapshot_id,
                history_cutoff_seq: manifest.history_cutoff_seq,
                projection_version: manifest.projection_version,
                manifest_digest: manifest.manifest_digest,
            })
        };
        let facts = crate::caller_dispatch_pipeline::DelegateDispatchFacts {
            environment: started.env.clone(),
            target_bot_id: started.target_bot_id.clone(),
            provider_id: context.provider_id.clone(),
            provider_bot_ref: context.provider_bot_ref.clone(),
            session_id: started.session_id.clone(),
            group_id: Some(started.group_id.clone()),
            message_turn_id: started.source_message_id.clone(),
            run_id,
            parent_run_id: parent_run,
            parent_attempt,
            parent_bot_id,
            attempt: started.attempt_no,
            dispatch_id,
            request_digest: digest,
            not_after_ms,
            caller_task,
            channel_actor_id,
            channel_actor_name,
            control_revision,
            // Purpose per the TARGET's own caller configuration (§8.3,
            // review P1-2): the runtime default is DelegationOnly — the
            // Execution split rides the row's trusted delegation facts
            // from the coordination edge's config query, never a blanket
            // blanket issuance (the edge writes `caller_execution_purpose`;
            // the delegate path reads it, defaulting closed).
            purpose: started
                .semantic_projection_json
                .get("caller_delegation")
                .and_then(|value| value.get("execution_purpose"))
                .and_then(serde_json::Value::as_str)
                .and_then(|value| match value {
                    "execution" => Some(bcs_service_api::application::caller_identity::CallerExecutionPurpose::Execution),
                    _ => None,
                })
                .unwrap_or(bcs_service_api::application::caller_identity::CallerExecutionPurpose::DelegationOnly),
            history_cutoff_seq: history_header
                .as_ref()
                .map(|history| history.history_cutoff_seq)
                .unwrap_or(0),
            delivery_commit: Some(crate::caller_dispatch_pipeline::DeliveryCommitOrigin {
                delivery_id: started.delivery_id.clone(),
                // The commit origin must be the POST-StartSend version: the
                // root path commits against `started` (§15.17), and a stale
                // pre-StartSend version CAS-refuses the commit forever —
                // caught by the wired-commit delegate e2e (§15.77).
                expected_state_version: started.state.state_version,
            }),
            history: history_header,
            shared_content_kind: bcs_domain::shared_stream::shared_kind_for_delivery(started.flow_kind),
        };
        tracing::info!(
            delivery_id = %started.delivery_id,
            bot_id = %started.target_bot_id,
            attempt = started.attempt_no,
            "caller delegate dispatch: lineage-bounded issue/ensure/send toward the worker"
        );
        let outcome = pipeline
            .dispatch_delegate_once(
                &context.target,
                &context.service_credential,
                facts,
                now_ms(),
            )
            .await;
        let current = match self
            .service
            .lookup(DeliveryLookup::Id(started.delivery_id.clone()))
            .await
        {
            Ok(rows) => rows
                .into_iter()
                .find(|row| row.delivery_id == started.delivery_id)
                .unwrap_or_else(|| started.clone()),
            Err(_) => started.clone(),
        };
        match outcome {
            Ok(crate::caller_dispatch_pipeline::CallerDispatchOutcome::Accepted) => {
                self.transition_ok(event(&current, Event::Submitted)).await;
            }
            Ok(crate::caller_dispatch_pipeline::CallerDispatchOutcome::Rejected { error_code, retryable }) => {
                // Spec §6.8 (review-V P1-3): align the CHILD branch with the
                // root's barrier semantics — a retryable HISTORY_NOT_READY is
                // a pre-acceptance refusal, NOT a transport rejection. The
                // row parks Dispatching under the same durable marker, its
                // committed digest/attempt intact, and only the same dispatch
                // is re-sent after the backfill.
                if error_code == "HISTORY_NOT_READY" && retryable {
                    tracing::info!(
                        delivery_id = %current.delivery_id,
                        bot_id = %current.target_bot_id,
                        "caller delegate dispatch: history not ready; same dispatch parked for redrive"
                    );
                    if let Err(error) = self
                        .service
                        .update_wait_reasons(
                            vec![(
                                current.delivery_id.clone(),
                                bcs_domain::message_delivery::DeliveryWaitReason::HistoryBarrier,
                            )],
                            now_ms(),
                        )
                        .await
                    {
                        tracing::warn!(
                            delivery_id = %current.delivery_id,
                            %error,
                            "history barrier marker persistence failed; the parked row loses restart protection"
                        );
                    }
                    return;
                }
                tracing::warn!(
                    delivery_id = %current.delivery_id,
                    bot_id = %current.target_bot_id,
                    error_code = %error_code,
                    retryable,
                    "caller delegate dispatch: provider rejected"
                );
                self.transition_ok(event(&current, Event::TransportRejected)).await;
            }
            Ok(crate::caller_dispatch_pipeline::CallerDispatchOutcome::DefinitelyNotSent) => {
                // Same evidence rule as the root branch: a 4xx non-ack or a
                // pre-wire refusal converges the delegate row failed without
                // the maybe-sent marker; the request id is the CAS evidence.
                let mut failed = event(&current, Event::DefinitelyNotSent { retry: false });
                failed.request_id = current.request_id.clone();
                self.transition_ok(failed).await;
            }
            Ok(crate::caller_dispatch_pipeline::CallerDispatchOutcome::OutcomeUnknown) => {
                self.transition_ok(event(&current, Event::TransportUnknown)).await;
            }
            Err(error) => {
                use crate::caller_dispatch_pipeline::DelegateDispatchError as E;
                match error {
                    // Nothing entered the network (bad input, no live
                    // lineage): the send is provably absent and the
                    // DefinitelyNotSent evidence rule applies. Logged here —
                    // this convergence arm was previously fully silent.
                    E::InvalidInput | E::LineageUnavailable => {
                        tracing::warn!(
                            delivery_id = %current.delivery_id,
                            bot_id = %current.target_bot_id,
                            error = %error,
                            "caller delegate dispatch converged not-sent before the network; the refusal is logged here"
                        );
                        let mut failed = event(&current, Event::DefinitelyNotSent { retry: false });
                        failed.request_id = current.request_id.clone();
                        self.transition_ok(failed).await;
                    }
                    // Container/issue stages hit the network but never sent
                    // the task: conservative convergence keeps the marker.
                    E::PreparationFailed | E::IssueFailed | E::CommitFailed => {
                        tracing::warn!(
                            delivery_id = %current.delivery_id,
                            bot_id = %current.target_bot_id,
                            "caller delegate dispatch: stage failure, converging failed"
                        );
                        self.transition_ok(event(&current, Event::Failed)).await;
                    }
                }
            }
        }
    }

}
