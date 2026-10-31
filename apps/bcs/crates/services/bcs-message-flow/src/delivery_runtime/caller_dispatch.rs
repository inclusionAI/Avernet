use super::*;

impl DeliveryRuntime {
    pub(super) async fn attempt_caller_protected_dispatch(&self, row: &PersistedMessageDelivery) {
        let Some(pipeline) = self.caller_dispatch.as_ref() else {
            tracing::warn!(
                delivery_id = %row.delivery_id,
                bot_id = %row.target_bot_id,
                "CALLER_EXECUTION_UNAVAILABLE: caller-required delivery rejected before preparation"
            );
            self.transition_ok(event(row, Event::PreparationFailed)).await;
            return;
        };
        // Real dispatch context (review P1-5): the row's own Provider
        // delivery target, its Provider-authorized service token and the
        // provider routing fields — never a placeholder WebSocket target
        // or a fixed credential. Resolution refuses fail-closed: the row
        // converges failed before any context read, exactly like the
        // digest guard.
        tracing::debug!(delivery_id = %row.delivery_id, stage = "resolve", "caller protected dispatch stage");
        let Some(context) = self.preparation.resolve_caller_dispatch(row).await else {
            tracing::warn!(
                delivery_id = %row.delivery_id,
                bot_id = %row.target_bot_id,
                "CALLER_EXECUTION_UNAVAILABLE: caller dispatch target does not resolve to a Provider container"
            );
            self.transition_ok(event(row, Event::PreparationFailed)).await;
            return;
        };
        // Wire facts are committed by the trusted producer; a missing or
        // malformed digest cannot be fabricated here, so the row stays
        // closed exactly like the guard.
        let pipeline_commits_digest = pipeline.managed_commit_wired();
        let preexisting_digest = row
            .semantic_projection_json
            .get("caller_request_digest")
            .and_then(serde_json::Value::as_str)
            .filter(|digest| !digest.is_empty())
            .map(str::to_string);
        let Some(digest) = preexisting_digest.clone().or_else(|| {
            // With the typed commit step wired the pipeline is the digest
            // producer after StartSend; a caller row without a preexisting
            // commit may still run, but the protected task text is required
            // for the canonical digest.
            pipeline_commits_digest.then(|| "sha256:produced-at-dispatch".to_string())
        }) else {
            tracing::warn!(
                delivery_id = %row.delivery_id,
                bot_id = %row.target_bot_id,
                "CALLER_EXECUTION_UNAVAILABLE: caller row carries no committed digest, staying closed"
            );
            self.transition_ok(event(row, Event::PreparationFailed)).await;
            return;
        };
        // Either branch above is a well-formed digest: a preexisting commit
        // is 71 chars by construction, and the placeholder marks that the
        // pipeline will recompute + CAS-persist the canonical digest after
        // StartSend (the facts-validation then skips the length check).
        let control_revision = row
            .semantic_projection_json
            .get("caller_control_revision")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(0);
        let caller_task = row
            .semantic_projection_json
            .get("caller_task")
            .and_then(serde_json::Value::as_str)
            .unwrap_or_default()
            .to_string();
        if caller_task.is_empty() {
            self.transition_ok(event(row, Event::PreparationFailed)).await;
            return;
        }
        // Trusted ordinary sender facts of the ORIGINAL message (fail closed
        // like the digest/task guards): the envelope's required `from` zone
        // derives from them. Without the fact the wire would ship `from: null`
        // (a definite Provider 400), so the row converges failed instead —
        // the protected caller subject never fills this ordinary zone.
        let Some((channel_actor_id, channel_actor_name)) = caller_channel_facts(row) else {
            tracing::warn!(
                delivery_id = %row.delivery_id,
                bot_id = %row.target_bot_id,
                "CALLER_EXECUTION_UNAVAILABLE: caller row carries no committed sender facts, staying closed"
            );
            self.transition_ok(event(row, Event::PreparationFailed)).await;
            return;
        };
        // The dispatch deadline is a committed fact, not a projection key
        // nobody writes (review P1-5): StartSend carries the row's run
        // budget (the same window the ordinary branch uses), the CAS
        // persists it as run_deadline_at_ms, and the committed row supplies
        // the protected not-after below.
        let dispatch_deadline = now_ms().saturating_add(
            run_timeout_for(row, self.config.run_timeout).as_millis() as i64
        );
        let mut start = event(row, Event::StartSend);
        start.deadline_at_ms = Some(dispatch_deadline);
        // The managed StartSend CAS validates the live policy version when
        // a durable policy exists — the same guard the ordinary branch
        // passes via prepared.transport_context_json. Without it every
        // caller StartSend rejects with Conflict and the tick retries the
        // resolve stage forever until the queue TTL lapses the row.
        //
        // The StartSend CAS also persists the row's transport metadata
        // (managed_delivery.rs:377), which resolve_managed_provider_run
        // and the Provider terminal arbiter read back to locate and
        // validate this row on late callbacks (owner/provider binding).
        // Protected dispatches must therefore commit the same
        // QueuedTransportContext layout the ordinary send-start persists —
        // built from the row's own resolved Provider scope, never guessed.
        let (_, _, _, policy_version) =
            self.policies(std::slice::from_ref(&row.target_bot_id)).await;
        {
            let transport = crate::queued_group::QueuedTransportContext {
                // The protected dispatch carries its own runtime-only
                // proof; the dev request-scoped cache credential never rides it.
                caller_expires_at_ms: None,
                version: 1,
                owner: bcs_service_api::port::chat_run::BotRunTransportOwner::from_target(&context.target),
                connection_id: None,
                downstream_session_key: row.session_id.clone(),
                downstream_run_id: None,
                cancel_reason: None,
                task_timeout: false,
                provider_route_headers: Vec::new(),
                relay_route_headers: None,
            };
            let mut transport_json = serde_json::to_value(transport)
                .map_err(|_| ())
                .unwrap_or_else(|_| serde_json::json!({}));
            if let Some(version) = policy_version {
                transport_json["policy_version"] = serde_json::json!(version);
            }
            start.transport_context_json = Some(transport_json);
        }
        // Delegate (worker) row (P5.9 dispatch): identity = the root's live
        // lineage (runtime-only) instead of a handoff claim. The trusted
        // task admission marked the assigning run; the pipeline looks the
        // lineage up, bounds by its window, issues a DelegationOnly proof
        // and routes the same wire — never the ordinary transport.
        let delegate_parent = row
            .semantic_projection_json
            .get("caller_delegation")
            .and_then(|value| value.get("parent_run_id"))
            .and_then(serde_json::Value::as_str)
            .filter(|value| !value.is_empty())
            .map(str::to_string);
        if delegate_parent.is_some() {
            self.attempt_caller_delegate_dispatch(
                pipeline,
                &context,
                digest,
                caller_task,
                control_revision,
                start,
                delegate_parent,
            )
            .await;
            return;
        }
        // Real attempt first (§7.5.1): StartSend CAS commits attempt/request
        // and the run budget that bounds the whole protected path.
        let Some(started) = self.transition_ok(start).await else {
            // Lost a CAS race (cancel/expiry): the row already converged.
            return;
        };
        let Some(run_id) = started.run_id.clone() else {
            self.transition_ok(event(&started, Event::DefinitelyNotSent { retry: false })).await;
            return;
        };
        let Some(dispatch_id) = started.request_id.clone() else {
            self.transition_ok(event(&started, Event::DefinitelyNotSent { retry: false })).await;
            return;
        };
        // The not-after is the JUST-COMMITTED deadline on the Started row
        // — never an admission-time projection key nobody produced. Network
        // is still untouched and the identity unclaimed: a missing budget
        // is a provable not-sent (the original request_id is the evidence).
        let Some(not_after_ms) = started.run_deadline_at_ms.filter(|deadline| *deadline > now_ms()) else {
            let mut failed = event(&started, Event::DefinitelyNotSent { retry: false });
            failed.request_id = started.request_id.clone();
            self.transition_ok(failed).await;
            return;
        };
        let commit_origin = crate::caller_dispatch_pipeline::DeliveryCommitOrigin {
            delivery_id: started.delivery_id.clone(),
            expected_state_version: started.state.state_version,
        };
        // P4.2 shared-history header (spec §6.8): group caller work carries
        // the snapshot facts — cutoff fixed at admission (the row's source
        // session seq, strictly before this message), membership from the
        // bound explicit id list, digest from the canonical manifest.
        let history_header = if started.semantic_projection_json.get("caller_required").is_some() {
            let contexts = self.service.bounded_contexts(&started.delivery_id, 25).await;
            // A read failure refuses the history header rather than
            // fabricating an empty manifest (fail closed, retryable later).
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
                format!("{}:{}", started.delivery_id, started.run_id.clone().unwrap_or_default()),
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
        } else {
            None
        };
        let facts = crate::caller_dispatch_pipeline::CommittedDispatchFacts {
            history: history_header,
            delivery_commit: Some(commit_origin),
            environment: started.env.clone(),
            target_bot_id: started.target_bot_id.clone(),
            // Real provider routing fields (review P1-5): the wire
            // association carries the row's actual Provider scope, resolved
            // before StartSend from the same registry the ordinary path
            // trusts.
            provider_id: context.provider_id.clone(),
            provider_bot_ref: context.provider_bot_ref.clone(),
            session_id: started.session_id.clone(),
            // P3.4: the group scope carries into the proof (spec §6.2 root
            // lineage per target; the turn stays the canonical message id).
            group_id: Some(started.group_id.clone()),
            message_turn_id: started.source_message_id.clone(),
            root_run_id: run_id.clone(),
            run_id,
            attempt: started.attempt_no,
            dispatch_id,
            request_digest: digest,
            not_after_ms,
            caller_task,
            channel_actor_id,
            channel_actor_name,
            control_revision,
            // L225 shared-stream classification: the admitted row's flow
            // kind decides the fan-out eligibility surface. Group chat rows
            // are OpenChat; any other (future) caller shape stays
            // unclassified and never broadcasts.
            shared_content_kind: bcs_domain::shared_stream::shared_kind_for_delivery(
                started.flow_kind,
            ),
        };
        tracing::info!(
            delivery_id = %started.delivery_id,
            bot_id = %started.target_bot_id,
            attempt = started.attempt_no,
            "caller protected dispatch: starting claim/prepare/issue/send"
        );
        let outcome = pipeline
            .dispatch_once(
                &context.target,
                &context.service_credential,
                facts,
                now_ms(),
            )
            .await;
        // The wired typed commit (§7.5.1) advances the row's state_version
        // inside the dispatch itself; converging transitions must re-read
        // the current row or every CAS goes stale and the row freezes in
        // Dispatching. Best-effort re-read: a vanished row keeps the facts.
        let current = match self
            .service
            .lookup(DeliveryLookup::Id(started.delivery_id.clone()))
            .await
        {
            Ok(rows) => rows.into_iter().find(|row| row.delivery_id == started.delivery_id).unwrap_or_else(|| started.clone()),
            Err(_) => started.clone(),
        };
        match outcome {
            Ok(crate::caller_dispatch_pipeline::CallerDispatchOutcome::Accepted) => {
                self.transition_ok(event(&current, Event::Submitted)).await;
            }
            Ok(crate::caller_dispatch_pipeline::CallerDispatchOutcome::Rejected { error_code, retryable }) => {
                // HISTORY_NOT_READY (spec §6.8): a retryable pre-acceptance
                // refusal — the Gateway kept the dispatch id bound to its
                // digest and awaits history completion. It is NOT a terminal
                // rejection for this dispatch: the row stays Dispatching
                // with its committed digest/attempt, identity is consumed
                // exactly once, and only the same dispatch is re-sent after
                // the history completes — no new attempt.
                if error_code == "HISTORY_NOT_READY" && retryable {
                    tracing::info!(
                        delivery_id = %started.delivery_id,
                        bot_id = %started.target_bot_id,
                        "caller protected dispatch: history not ready; same dispatch retried after completion"
                    );
                    // Stay Dispatching: no state transition, no Failed, no
                    // release. The row parks under the durable HistoryBarrier
                    // marker — the startup recovery sweep must not eat it
                    // (managed apply refuses Event::Recover on this marker)
                    // — until the backfill completes and the SAME dispatch
                    // is re-sent; the run-deadline control stays the
                    // convergence bound.
                    // Best-effort marker: losing it only costs the row its
                    // restart-sweep protection, never the dispatch facts.
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
                    delivery_id = %started.delivery_id,
                    bot_id = %started.target_bot_id,
                    retryable,
                    "caller protected dispatch: provider rejected"
                );
                self.transition_ok(event(&current, Event::TransportRejected)).await;
            }
            Ok(crate::caller_dispatch_pipeline::CallerDispatchOutcome::DefinitelyNotSent) => {
                // 4xx non-ack / 出栈前失败：确定性"未被受理"证据。行判死并
                // 清掉 may_have_been_sent（上下文 Release 而非 Consume），
                // lane 立即释放，不再等待终态回执。request_id 是 §7.5.1 的
                // owing-dispatcher 证据，随 CAS 提交。
                let mut failed = event(&current, Event::DefinitelyNotSent { retry: false });
                failed.request_id = current.request_id.clone();
                self.transition_ok(failed).await;
            }
            Ok(crate::caller_dispatch_pipeline::CallerDispatchOutcome::OutcomeUnknown) => {
                self.transition_ok(event(&current, Event::TransportUnknown)).await;
            }
            Err(error) => match error {
                // Nothing entered the network before the claim: the send is
                // provably absent. This arm WAS fully silent — the 2026-10-25
                // incident spent a whole day on it; it now carries the error.
                crate::caller_dispatch_pipeline::CallerDispatchError::InvalidInput
                | crate::caller_dispatch_pipeline::CallerDispatchError::IdentityUnavailable => {
                    tracing::warn!(
                        delivery_id = %current.delivery_id,
                        bot_id = %current.target_bot_id,
                        error = %error,
                        "caller protected dispatch converged not-sent before the claim; the identity/handoff/shape refusal is logged here"
                    );
                    // The DefinitelyNotSent evidence rule requires the
                    // original request_id (owing-dispatcher proof, §7.5.1).
                    let mut failed = event(&current, Event::DefinitelyNotSent { retry: false });
                    failed.request_id = current.request_id.clone();
                    self.transition_ok(failed).await;
                }
                // Container/proof stages hit the network but never issued the
                // chat.send: conservative convergence keeps the sent marker.
                other => {
                    tracing::warn!(
                        delivery_id = %started.delivery_id,
                        bot_id = %started.target_bot_id,
                        %other,
                        "caller protected dispatch: stage failure, converging failed"
                    );
                    self.transition_ok(event(&started, Event::Failed)).await;
                }
            },
        }
    }

}
