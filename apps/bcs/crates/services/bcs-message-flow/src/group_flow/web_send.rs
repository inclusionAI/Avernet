use super::*;

/// F4 parity for the NON-managed path (review R1): when the queue
/// admission did not select a Session but eventing persistence
/// WILL anchor this message to one (eventing-enabled
/// Chat/ManagerWorker send), resolve the SAME Session up front —
/// the exact lookup `try_persist_group_message` performs — so the
/// sender-membership gate below sees the actual write target and
/// the resolved id flows unchanged to routing, persistence and
/// delivery. Group-only sends (no eventing, or a StateMachine
/// Group, whose persistence never anchors to a Session) resolve
/// to `None` and keep their legacy shape. Without any Running
/// Session the eventing contract fails closed with the same error
/// persistence would raise — before ANY side effect, not after.
pub(super) async fn resolve_unmanaged_eventing_session(
    flow: &BcsMessageFlow,
    group: &Group,
) -> ServiceResult<Option<String>> {
    if flow.event_record_factory.is_none() || group.group_strategy == GroupStrategy::StateMachine {
        return Ok(None);
    }
    let session_management = flow.session_management.as_ref().ok_or_else(|| {
        ServiceError::InternalError(
            "Eventing message persistence requires session management".to_string(),
        )
    })?;
    let session = session_management
        .list_by_group(&group.id, Some(SessionStatus::Running), 0, 1, None, None)
        .await
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .into_iter()
        .next()
        .ok_or_else(|| ServiceError::InvalidOperation {
            message: format!(
                "cannot persist message.created for Group '{}' without a running Session",
                group.id
            ),
            request_id: None,
        })?;
    Ok(Some(session.id))
}

/// F4 target-membership gate for a Session the admission layer
/// selected implicitly (no explicit `session_id` reached this
/// boundary). The Session is read once through Session
/// management; a missing row or storage failure is an error —
/// never a membership bypass. Rejection happens BEFORE any
/// status reactivation, routing or persistence side effect.
pub(super) async fn ensure_resolved_session_sender_membership(
    flow: &BcsMessageFlow,
    session_id: &str,
    from_actor_id: &str,
) -> ServiceResult<()> {
    let session = flow
        .session_management
        .as_ref()
        .ok_or_else(|| {
            ServiceError::InternalError("queue Session management unavailable".into())
        })?
        .get(session_id)
        .await
        .map_err(|error| {
            ServiceError::InternalError(format!("queue Session membership read failed: {error}"))
        })?
        .ok_or_else(|| {
            ServiceError::InternalError(format!(
                "queue admission resolved unknown Session '{session_id}'"
            ))
        })?;
    match session
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == from_actor_id)
    {
        Some(participant) if participant.effective_mode() != ParticipantMode::Absent => {
            Ok(())
        }
        _ => Err(ServiceError::Forbidden(format!(
            "sender '{from_actor_id}' is not a participant of the resolved Session"
        ))),
    }
}

pub async fn handle_web_send(
    flow: &BcsMessageFlow,
    cmd: WebSendCommand,
) -> ServiceResult<WebSendOutcome> {
    handle_web_send_with_result(flow, cmd, None).await
}

pub(super) async fn handle_web_send_with_result(
    flow: &BcsMessageFlow,
    mut cmd: WebSendCommand,
    result_message: Option<&state_machine_result::ResultMessage>,
) -> ServiceResult<WebSendOutcome> {
    // Authentication and execution identity are separate types, but a Human
    // submission must bind both to the same sender before any side effect.
    // (Identity carries no expiry window — the authenticated actor binding
    // is the only seam check; session validity is the login lifecycle.)
    if let Some(ingress) = cmd.caller_ingress.as_ref() {
        let matches_principal = matches!(&cmd.caller,
            CallerContext::Human(actor) if actor == ingress.actor());
        if !matches_principal || cmd.from_actor_id != ingress.actor().actor_id
            || ingress.env() != bcs_config::resolve_env_str()
        {
            return Err(ServiceError::InvalidOperation {
                message: "caller identity mismatch".into(), request_id: None,
            });
        }
    }
    log_message_received(&cmd, MessageLogMode::FreeChat);

    let mut group = flow
        .group
        .get(&cmd.group_id)
        .await
        .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;

    // F4: when the caller omitted session_id, SOME downstream writer
    // selects a Session on the sender's behalf — the queue admission
    // for managed Groups, message persistence for managed AND non-
    // managed eventing Groups — but the entrypoint could then only
    // authorize the GROUP target. Resolve that same actual write
    // target up front and gate the sender's membership of it before
    // any side effect: a sender removed from (or never in) that
    // Session must not land new messages in it, and persistence must
    // never silently pick a different one later. An explicit
    // session_id is already gated by the entrypoint's strict target
    // authorization and takes the same read no more.
    let session_for_admission = cmd.session_id.take();
    let implicit_resolution = session_for_admission.is_none();
    cmd.session_id =
        crate::queued_admission::resolve_group_session(flow, &group, session_for_admission).await?;
    if implicit_resolution && cmd.session_id.is_none() {
        cmd.session_id = resolve_unmanaged_eventing_session(flow, &group).await?;
    }
    if implicit_resolution {
        if let Some(session_id) = cmd.session_id.as_deref() {
            ensure_resolved_session_sender_membership(flow, session_id, &cmd.from_actor_id)
                .await?;
        }
    }
    // F1 (bot-authority spec §8.2): assign THIS operation its output
    // identity and freeze per-connection output permits BEFORE the
    // operation's first side effect (the status reactivation below).
    // The sender's own authorization already passed at the strict
    // target gates above; passive bystanders failing their admission
    // simply receive no permit while the accepted operation proceeds.
    // A later duplicate (idempotent) admission produces no new frames
    // for this identity, and its unused prepared permits expire on
    // the adapter's prepare-timeout — never by evicting live permits.
    let operation_id = uuid::Uuid::new_v4().to_string();
    if let Some(output_admission) = flow.output_admission.as_ref() {
        output_admission
            .admit_output_operation(bcs_service_api::OutputOperationAdmission {
                operation_id: operation_id.clone(),
                group_id: cmd.group_id.clone(),
                session_id: cmd.session_id.clone(),
            })
            .await?;
    }
    if group.status == GroupStatus::Inactive {
        crate::update_group_status(
            flow.group.as_ref(),
            &cmd.group_id,
            GroupStatus::Active,
            "message_activity_resumed",
            crate::caller_event_actor(&cmd.caller),
        )
        .await?;
        group = flow
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;
    }

    if group.status != GroupStatus::Active {
        return Err(ServiceError::InvalidOperation {
            message: format!("group '{}' is not active", cmd.group_id),
            request_id: None,
        });
    }

    flow.group.reset_message_count(&cmd.group_id).await?;
    apply_session_participant_scope(flow, &mut group, cmd.session_id.as_deref()).await?;
    backfill_bot_names(flow.registry.as_ref(), &mut group).await;

    let overlay = build_route_overlay(flow, &group).await;
    let decision = if group.group_kind == GroupKind::Dm {
        flow.routing
            .route_dm_with_overlay(&group, &cmd.message, &cmd.from_actor_id, &overlay)
            .await
    } else if cmd.mentions.is_empty() {
        flow.routing
            .route_with_overlay(&group, &cmd.message, None, &overlay)
            .await
    } else {
        build_explicit_mention_decision(&group, &cmd.mentions, &cmd.message, &overlay)
    };

    if result_message.is_some() {
        if !matches!(group.group_strategy, GroupStrategy::Chat | GroupStrategy::ManagerWorker)
            || !group.participants.iter().any(|p| p.is_bot() && p.bot_uuid == cmd.from_actor_id) {
            return Err(ServiceError::InvalidOperation {
                message: "Chat result requires its original sender in a Chat or ManagerWorker Session".into(),
                request_id: None,
            });
        }
        if !decision.targets.iter().any(|target| target.delivery_type == DeliveryType::Send) {
            return Err(ServiceError::InvalidOperation {
                message: "Chat result has no reply-producing target".into(), request_id: None,
            });
        }
    }

    if let Some(caller) = &cmd.caller_execution {
        // A channel caller is never a general-purpose credential for routing fan-out.
        if group.group_strategy != GroupStrategy::Chat || decision.targets.len() != 1
            || decision.targets[0].bot_uuid != caller.target_bot_id
            || decision.targets[0].delivery_type != DeliveryType::Send
            || !flow.registry.resolve_delivery_target(&caller.target_bot_id).await?.is_http_provider() {
            return Err(ServiceError::InvalidOperation {
                message: "caller execution requires one direct HTTP provider Bot".into(), request_id: None,
            });
        }
        crate::caller_execution::store(flow, &cmd).await?;
    }

    log_routing_digest(
        &cmd,
        &decision,
        MessageLogMode::FreeChat,
        route_source_for_web_send(&group, &cmd),
    );

    let sender_display_name = preferred_sender_display_name(flow, &cmd).await;
    let from_bot_owner = from_bot_owner(flow, &cmd.from_actor_id).await;
    let (mention_source, mention_message_text): (Option<&[String]>, String) =
        if group.group_kind == GroupKind::Dm {
            (None, String::new())
        } else if !cmd.mentions.is_empty() {
            // 显式 mention 路径：使用人类发送者看到的原始消息文本。
            (Some(cmd.mentions.as_slice()), cmd.message.clone())
        } else {
            (
                Some(decision.mentions.as_slice()),
                decision.cleaned_message.clone(),
            )
        };
    let sender_type = if cmd.from_actor_id.starts_with("human_") {
        SenderType::Human
    } else {
        SenderType::Bot
    };
    // Resolve caller-enabled targets for a request authenticated as a Human,
    // even when the effective sender is one of that Human's owned Bots. The
    // guard below then requires that an enabled target be addressed by real
    // Human speech: a Human must not bypass caller work by proxying a Bot.
    let caller_admission_targets: Vec<String> = if matches!(&cmd.caller, CallerContext::Human(_)) {
        match flow.caller_admission.as_ref() {
            Some(deps) => {
                let env = bcs_config::resolve_env_str();
                if group.group_kind == GroupKind::Dm {
                    vec![command_targets_first_bot(&decision).to_string()]
                } else {
                    // Per-target configuration check (§15.96 batch): ONE
                    // round trip for the whole member scan (plus the routed
                    // target when it differs), not one per participant.
                    // Only caller-enabled member bots (plus the routed
                    // target if enabled) become admission targets; plain
                    // groups stay ordinary.
                    let participants: Vec<String> = group
                        .participants
                        .iter()
                        .filter(|p| p.is_bot())
                        .map(|p| p.bot_uuid.clone())
                        .collect();
                    let routed = command_targets_first_bot(&decision).to_string();
                    let mut batch = participants.clone();
                    if !routed.is_empty() {
                        batch.push(routed.clone());
                    }
                    let configs = deps
                        .control_plane
                        .get_caller_configurations(&batch, &env)
                        .await
                        .map_err(|_| ServiceError::InvalidOperation {
                            message: "caller configuration unavailable".into(), request_id: None,
                        })?;
                    let is_enabled = |bot_id: &str| {
                        configs
                            .iter()
                            .find(|(id, _)| id == bot_id)
                            .and_then(|(_, config)| config.snapshot.as_ref())
                            .is_some_and(|snapshot| snapshot.caller_identity.enabled)
                    };
                    let enabled: Vec<String> = participants
                        .iter()
                        .filter(|bot_id| is_enabled(bot_id))
                        .cloned()
                        .collect();
                    let enabled = if !routed.is_empty() && !enabled.contains(&routed) && is_enabled(&routed) {
                        let mut with_routed = enabled;
                        with_routed.push(routed);
                        with_routed
                    } else {
                        enabled
                    };
                    enabled
                }
            }
            None => Vec::new(),
        }
    } else {
        Vec::new()
    };
    // Resolve enabled targets once (the group gate below reuses the same set).
    let caller_admission_targets: Vec<String> = if group.group_kind == GroupKind::Dm {
        // DM: original single-target check
        match flow.caller_admission.as_ref() {
            Some(deps) if matches!(&cmd.caller, CallerContext::Human(_)) => {
                let target_enabled = deps
                    .control_plane
                    .get_caller_configuration(
                        command_targets_first_bot(&decision),
                        &bcs_config::resolve_env_str(),
                    )
                    .await
                    .map_err(|_| ServiceError::InvalidOperation {
                        message: "caller configuration unavailable".into(), request_id: None,
                    })?
                    .is_some_and(|config| config.snapshot.is_some_and(|s| s.caller_identity.enabled));
                if target_enabled { caller_admission_targets } else { Vec::new() }
            }
            _ => Vec::new(),
        }
    } else {
        caller_admission_targets
    };
    // Caller mode follows target configuration, never the presence of identity.
    // A stripped/expired WS snapshot must not become ordinary Owner work, and
    // a Human speaking as an owned Bot must not include an enabled target in
    // ordinary execution merely to avoid carrying the Human's identity.
    if !caller_admission_targets.is_empty() {
        if sender_type != SenderType::Human {
            return Err(ServiceError::InvalidOperation {
                message: "caller target requires human speech".into(),
                request_id: None,
            });
        }
        if cmd.caller_ingress.is_none() {
            return Err(ServiceError::InvalidOperation {
                message: "caller identity missing".into(),
                request_id: None,
            });
        }
    }
    let caller_deps = if caller_admission_targets.is_empty() { None } else { flow.caller_admission.clone() };
    // Owner→caller identity relay (spec L62 direction): with the deployment's
    // allowlist naming a relay-capable middle hop among the routed
    // participants, the authenticated Human's ingress is captured against
    // the session's current turn — memory-only, before any ordinary
    // execution. The middle bot receives nothing here; the later trusted
    // task-assign edge is the only redeemer. A capture that fails (ledger
    // saturated with live captures) warns and continues: the message still
    // runs its ordinary course, and the relay-side stays fail-closed rather
    // than failing the Human's speech on housekeeping.
    // P3.2 speaking lease (spec §6.1/§6.3): only Human speech in CALLER
    // groups is fenced — plain groups (no Human member, or no bot with a
    // caller-enabled configuration) never read or write the lease store.
    // An enabled-but-blocked bot still fences: selecting the fence is not
    // a downgrade into an ordinary group.
    // Review 2.4: the group-type judgment is INDEPENDENT of whether this
    // submission carries strict caller ingress — a caller group fences
    // Human speech no matter how the message authenticated, so an
    // ingress-less Human message can never slip the fence. A control-plane
    // configuration read failure is fail-closed (the mode is UNDECIDABLE,
    // not Plain); groups without Human members stay Plain without a single
    // configuration read.
    // The relay capture decision reuses this scan: the accumulators stay in
    // scope beyond the fence below (whose per-participant reads the
    // caller-config cache already bounds).
    let mut any_bot_caller_enabled = false;
    let mut mode_undecidable = false;
    let speaking_fence = if sender_type == SenderType::Human
        && group.group_kind != GroupKind::Dm
    {
        let has_human_member = group
            .participants
            .iter()
            .any(|participant| participant.actor_kind == ActorKind::Human);
        if has_human_member {
            let env = bcs_config::resolve_env_str();
            if let Some(deps) = flow.caller_admission.as_ref() {
                for participant in group.participants.iter().filter(|p| p.is_bot()) {
                    match deps
                        .control_plane
                        .get_caller_configuration(&participant.bot_uuid, &env)
                        .await
                    {
                        // A read failure is never "not enabled": the lease
                        // mode must not fail open (a human message would
                        // bypass the fence exactly when the control plane
                        // is broken).
                        Err(_) => {
                            mode_undecidable = true;
                            break;
                        }
                        Ok(None) => {}
                        Ok(Some(config)) => {
                            let enabled_here = config
                                .snapshot
                                .as_ref()
                                .is_some_and(|snapshot| snapshot.caller_identity.enabled);
                            if enabled_here {
                                any_bot_caller_enabled = true;
                                break;
                            }
                        }
                    }
                }
            }
        }
        if mode_undecidable {
            return Err(ServiceError::InvalidOperation {
                message: "caller configuration unavailable; speaking-lease mode is undecidable"
                    .into(),
                request_id: None,
            });
        }
        match bcs_domain::speaking_lease::resolve_speaking_mode(has_human_member, any_bot_caller_enabled) {
            bcs_domain::speaking_lease::SessionSpeakingMode::Plain => None,
            bcs_domain::speaking_lease::SessionSpeakingMode::LeaseRequired => {
                // Fail closed when the composition cannot decide leases.
                let repo = flow.speaking_leases.as_ref().ok_or_else(|| {
                    ServiceError::InvalidOperation {
                        message: "speaking lease required but unavailable".into(),
                        request_id: None,
                    }
                })?;
                let claim = cmd.speaking_lease.as_ref().ok_or_else(|| {
                    ServiceError::InvalidOperation {
                        message: "speaking lease required".into(),
                        request_id: None,
                    }
                })?;
                Some((repo.clone(), claim.clone()))
            }
        }
    } else {
        None
    };
    // Owner→caller identity relay (spec L62 direction, DEFAULT-ON): any
    // session that carries at least one caller-enabled member becomes
    // relay-capable — the authenticated Human's ingress is captured into
    // the session-keyed ledger and the two trusted exchange points (the
    // task-assign edge, orchestration submission) are the only redeemers.
    // No per-edge policy exists: the target side is governed by the bot's
    // own caller-enabled configuration and the capture is bounded by TTL,
    // per-turn budget and epoch seals. A refused capture (ledger saturated
    // with live captures) warns and continues — the message runs its
    // ordinary course and the relay-side stays fail-closed.
    if let (Some(relay), true) = (
        flow.caller_relay.as_ref(),
        sender_type == SenderType::Human
            && group.group_kind != GroupKind::Dm
            && any_bot_caller_enabled,
    ) {
        if let Some(ingress) = cmd.caller_ingress.as_ref() {
            let capture_session = cmd.session_id.clone().unwrap_or_else(|| cmd.group_id.clone());
            let capture_turn = cmd
                .source_im_message_id
                .clone()
                .or_else(|| cmd.idempotency_key.clone())
                .unwrap_or_else(|| capture_session.clone());
            let captured = relay.ledger.record(
                &bcs_config::resolve_env_str(),
                &capture_session,
                &capture_turn,
                ingress.clone(),
                now_ms() as i64,
                relay.capture_ttl_ms,
            );
            if captured {
                tracing::info!(
                    session = %capture_session,
                    turn = %capture_turn,
                    "caller relay captured the session's human ingress (bounded by TTL and per-turn redeem budget)"
                );
            } else {
                tracing::warn!(
                    session = %capture_session,
                    "caller relay capture refused (ledger saturated with live captures); later relays stay closed"
                );
            }
        }
    }
    // Submission eligibility fence BEFORE durable acceptance (spec §6.3
    // rule 2): a message that passed the fence before a preemption
    // boundary stays legal; a claim losing the race is refused entry.
    // The lease key is the GROUP (spec §6.3: one Human voice per caller
    // group); DM traffic never fences. cmd.session_id may already be
    // resolved to a session-scoped id here — deliberately not used as the
    // lease key.
    let speaking_claim = speaking_fence.as_ref().map(|(repo, claim)| {
        (repo.clone(), cmd.group_id.clone(), claim.clone())
    });
    if let Some((repo, session, claim)) = speaking_claim.as_ref() {
        let holder = cmd.from_actor_id.clone();
        let decision = repo
            .check_submission(bcs_service_api::port::repo::session_speaking_lease::AdmitMessage {
                session_id: session,
                expected_holder: &holder,
                expected_lease_id: &claim.lease_id,
                expected_epoch: claim.epoch,
                now_ms: now_ms().max(0) as i64,
                idle_timeout_ms: flow.speaking_lease_idle_timeout_ms,
            })
            .await?;
        match decision {
            bcs_service_api::port::repo::session_speaking_lease::LeaseDecision::Admitted(_) => {}
            other => {
                // The closed reason kinds: superseded/wrong-holder/lost —
                // actionable for the client, never identity material.
                let kind = match &other {
                    bcs_service_api::port::repo::session_speaking_lease::LeaseDecision::Admitted(_) => unreachable!(),
                    bcs_service_api::port::repo::session_speaking_lease::LeaseDecision::Superseded { .. } => "superseded",
                    bcs_service_api::port::repo::session_speaking_lease::LeaseDecision::WrongHolder { .. } => "wrong_holder",
                    bcs_service_api::port::repo::session_speaking_lease::LeaseDecision::Lost => "lost",
                };
                return Err(ServiceError::InvalidOperation {
                    message: format!("speaking lease rejected the submission: {kind}"),
                    request_id: None,
                })
            }
        }
    }
    let freeze_session = session_snapshot_for_freeze(flow, cmd.session_id.as_deref()).await;
    let prepared_command = crate::queued_admission::prepare_group_admission_with_message(flow, &group, &cmd, &decision,
        &sender_display_name, from_bot_owner.clone(), result_message.map(|result| (result.id.as_str(), &result.message))).await?;
    // F1 (spec §8.2 — recheck R6): the managed row becomes runnable
    // THE MOMENT the durable admission commits (the queue publishes
    // it before this call returns, and any scan may pick it up).
    // So every pre-assigned managed run's operation binding and
    // frozen producer facts exist BEFORE the commit below — a
    // worker emitting under this run id always resolves the
    // trusted operation, never a fresh per-event authorization,
    // even when this caller is paused or cancelled at the commit
    // boundary. Bindings are idempotent: they merely must precede
    // the commit.
    if let Some(command) = prepared_command.as_ref() {
        for target in &command.targets {
            let Some(run_id) = target.run_id.as_deref() else {
                continue;
            };
            flow.message_tracker
                .cache_run_operation(run_id, &operation_id)
                .await;
            freeze_run_output_facts(
                flow,
                &group,
                freeze_session.as_ref(),
                run_id,
                &target.target_bot_id,
            )
            .await;
        }
    }
    // F1 (spec §8.2 — recheck R9): the operation's COMPLETE
    // output-stream set (managed ∪ direct) is planned and
    // registered BEFORE the durable commit below — the queue worker
    // may start any managed run the moment the admission commits,
    // and this sender may be paused, killed or cancelled right
    // there. Registering only after the caller continued would
    // leave a committed run whose operation permits sit prepared
    // (sweepable on the prepare timeout) while durable work flows.
    // Commit-side failure, duplicates and admission-time rejections
    // release the pre-registered identities exactly below.
    enum TargetPlan {
        Deliver {
            run_id: String,
            target_bot_id: String,
            delivery_type: DeliveryType,
            delivery_target: BotDeliveryTarget,
            frame: BcsFrame,
        },
        Rejected {
            summary: MessageDeliveryResult,
            delivery: BotDeliveryResult,
        },
    }

    let mut plans = Vec::new();
    for target in &decision.targets {
        if prepared_command.as_ref().is_some_and(|command| command.targets.iter().any(|t| t.target_bot_id == target.bot_uuid)) {
            continue;
        }
        let run_id = uuid::Uuid::new_v4().to_string();
        let target_bot_id = target.bot_uuid.clone();
        let delivery_type = target.delivery_type;
        let outbound_candidate = GroupMessage {
            id: run_id.clone(),
            timestamp: now_ms(),
            sender: cmd.from_actor_id.clone(),
            content: decision.cleaned_message.clone(),
            message_type: GroupMessageType::Bot,
            bot_name: Some(sender_display_name.clone()),
            role: MessageRole::User,
            run_id: String::new(),
            history_meta: None,
            metadata: None,
            attachments: None,
        };
        let outbound_message =
            match apply_outbound_interceptors(flow, &cmd.group_id, &outbound_candidate, target)
                .await
            {
                Ok(message) => message,
                Err(reason) => {
                    log_bot_deliver_result(
                        &cmd.group_id,
                        cmd.session_id.as_deref(),
                        &run_id,
                        &target_bot_id,
                        delivery_type,
                        false,
                        Some(reason.message.as_str()),
                        Some("interceptor"),
                    );
                    plans.push(TargetPlan::Rejected {
                        summary: delivery_result_summary(
                            &target_bot_id,
                            delivery_type,
                            false,
                            Some(reason.message.clone()),
                        ),
                        delivery: BotDeliveryResult {
                            target_bot_id,
                            delivered: false,
                            error: Some(ServiceError::Unauthorized(reason.message)),
                        },
                    });
                    continue;
                }
            };
        let delivery_target = match flow.registry.resolve_delivery_target(&target_bot_id).await {
            Ok(target) => target,
            Err(error) => {
                let error_text = error.to_string();
                log_bot_deliver_result(
                    &cmd.group_id,
                    cmd.session_id.as_deref(),
                    &run_id,
                    &target_bot_id,
                    delivery_type,
                    false,
                    Some(error_text.as_str()),
                    Some("resolve_target"),
                );
                plans.push(TargetPlan::Rejected {
                    summary: delivery_result_summary(
                        &target_bot_id,
                        delivery_type,
                        false,
                        Some(error_text),
                    ),
                    delivery: BotDeliveryResult {
                        target_bot_id,
                        delivered: false,
                        error: Some(error),
                    },
                });
                continue;
            }
        };
        let frame = frame_for_target(
            flow,
            &group,
            &cmd,
            &decision,
            &target,
            &delivery_target,
            &run_id,
            &outbound_message.content,
            &sender_display_name,
            from_bot_owner.clone(),
        )
        .await;
        // FIXME(interceptor-modify): outbound_message.id and metadata are not
        // threaded into the frame. SecurityInterceptor rewrites .id with the
        // gateway-issued task_id, but downstream still sees run_id. Tracked
        // in the Phase-5 follow-up list (Modify semantics completeness).
        plans.push(TargetPlan::Deliver {
            run_id,
            target_bot_id,
            delivery_type,
            delivery_target,
            frame,
        });
    }

    // Bind every event-producing run's operation tag, freeze every
    // producer's output facts from the admission-time snapshot,
    // and register the operation's COMPLETE output-stream set
    // (managed ∪ direct) BEFORE the durable commit below — a queue
    // worker can start any managed run the moment it commits, and
    // only Send targets produce callbacks (Inject never registers a
    // run context), so only they count towards completion.
    let mut operation_run_ids: Vec<String> = Vec::new();
    if let Some(command) = prepared_command.as_ref() {
        // Pre-assigned at prepare time — only executable Send
        // targets carry one (admission-time rejections stay
        // unassigned).
        operation_run_ids
            .extend(command.targets.iter().filter_map(|target| target.run_id.clone()));
    }
    let mut direct_send_ids = Vec::new();
    for plan in &plans {
        if let TargetPlan::Deliver {
            run_id,
            target_bot_id,
            delivery_type: DeliveryType::Send,
            ..
        } = plan
        {
            flow.message_tracker
                .cache_run_operation(run_id, &operation_id)
                .await;
            freeze_run_output_facts(
                flow,
                &group,
                freeze_session.as_ref(),
                run_id,
                target_bot_id,
            )
            .await;
            direct_send_ids.push(run_id.clone());
        }
    }
    operation_run_ids.extend(direct_send_ids.iter().cloned());
    if let Some(output_admission) = flow.output_admission.as_ref() {
        if !operation_run_ids.is_empty() {
            output_admission
                .register_operation_runs(&operation_id, operation_run_ids.clone())
                .await?;
        }
    }

    let admission = match prepared_command {
        Some(command) => match caller_deps {
            None => {
            let admitted = flow
                .managed_deliveries
                .as_ref()
                .ok_or_else(|| ServiceError::InternalError("queue service unavailable".into()))?
                .admit(command)
                .await;
            match admitted {
                Ok(result) => Some(result),
                Err(error) => {
                    use bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError;
                    use bcs_service_api::ManagedDeliveryError;
                    // Only an explicit admission rejection proves no commit.
                    // Storage/unknown errors may be lost COMMIT acknowledgements:
                    // retain managed run identities and frozen output permits.
                    // Direct targets have not started I/O and can be released.
                    let rejected = matches!(
                        error,
                        ManagedDeliveryError::Conflict
                            | ManagedDeliveryError::Repository(
                                MessageDeliveryRepoError::Conflict
                                    | MessageDeliveryRepoError::Invalid(_)
                            )
                    );
                    let unused = if rejected { &operation_run_ids } else { &direct_send_ids };
                    flow.release_output_runs(&operation_id, unused).await;
                    warn!(%operation_id, %error, rejected,
                        "queue admission failed; only proven unused runs released");
                    return Err(ServiceError::InternalError(
                        "queue admission persistence failed".into(),
                    ));
                }
            }
            }
            Some(deps) => {
                    let ingress = cmd.caller_ingress.take()
                        .ok_or_else(|| ServiceError::InvalidOperation { message: "caller identity missing".into(), request_id: None })?;
                    // Per-target scopes: only targets present in the prepared
                    // delivery command (a missing enabled target is simply
                    // not routed this round). DM keeps the classic shape; a
                    // group puts the first enabled target as primary and the
                    // rest as extras (all marked, all published atomically).
                    let make_scope = |target_bot_id: &str| {
                        bcs_service_api::application::caller_handoff::HandoffScope {
                            env: bcs_config::resolve_env_str(),
                            session_id: command.message.session_id.clone(),
                            group_id: Some(command.message.group_id.clone()),
                            source_message_id: command.message_id.clone(),
                            target_bot_id: target_bot_id.to_string(),
                        }
                    };
                    let routed_targets: Vec<String> = command
                        .targets
                        .iter()
                        .filter(|target| {
                            caller_admission_targets.is_empty()
                                || caller_admission_targets.contains(&target.target_bot_id)
                        })
                        .map(|target| target.target_bot_id.clone())
                        .collect();
                    // §7.2.1 delegation-only scopes (review batch-3 §3.1):
                    // the caller-DISABLED routed targets of a lease-admitted
                    // caller group (the fence above proved the lease; the
                    // group gate keeps DM — 私聊 Owner A 不初始化 lineage).
                    // These targets keep the ordinary Owner execution; their
                    // row carries only the trusted `caller_lineage` marker
                    // and their identity parks for the runtime's delegation-
                    // only root registration.
                    let delegation_scopes: Vec<_> = if speaking_fence.is_some()
                        && group.group_kind != GroupKind::Dm
                    {
                        command
                            .targets
                            .iter()
                            .map(|target| target.target_bot_id.clone())
                            .filter(|target_bot_id| {
                                !routed_targets.contains(target_bot_id)
                                    && group.participants.iter().any(|participant| {
                                        participant.is_bot()
                                            && participant.bot_uuid == *target_bot_id
                                    })
                            })
                            .map(|target_bot_id| make_scope(&target_bot_id))
                            .collect()
                    } else {
                        Vec::new()
                    };
                    // No caller-enabled target routed and no Owner
                    // association target either: nothing the seam owns.
                    if routed_targets.is_empty() && delegation_scopes.is_empty() {
                        return Err(ServiceError::InvalidOperation { message: "caller admission requires one delivery target".into(), request_id: None });
                    }
                    let scope = routed_targets.first().map(|target_bot_id| make_scope(target_bot_id));
                    let extra_scopes: Vec<_> = routed_targets
                        .iter()
                        .skip(1)
                        .map(|target_bot_id| make_scope(target_bot_id))
                        .collect();
                    let caller_message = crate::caller_admission::CallerMessageAdmission {
                        scope,
                        extra_scopes,
                        delegation_scopes,
                        ingress,
                        sender_name: sender_display_name.clone(),
                        admission: command,
                    };
                    let now = now_ms().max(0) as i64;
                    let outcome = crate::caller_admission::admit_caller_message(&deps, caller_message, now)
                        .await
                        .map_err(caller_admission_error)?;
                    Some(outcome.result)
            }
        }
        None => None,
    };
    // Duplicates replay their original acceptance and NEVER renew the
    // idle window; a fresh acceptance is the only renewal event, and a
    // renewal decision lost after acceptance never un-accepts the message.
    if admission.as_ref().is_some_and(|result| !result.duplicate) {
        if let Some((repo, session, claim)) = speaking_claim.as_ref() {
            let renewal = repo
                .admit_message(bcs_service_api::port::repo::session_speaking_lease::AdmitMessage {
                    session_id: session,
                    expected_holder: &cmd.from_actor_id,
                    expected_lease_id: &claim.lease_id,
                    expected_epoch: claim.epoch,
                    now_ms: now_ms().max(0) as i64,
                    idle_timeout_ms: flow.speaking_lease_idle_timeout_ms,
                })
                .await;
            if !matches!(
                renewal,
                Ok(bcs_service_api::port::repo::session_speaking_lease::LeaseDecision::Admitted(_))
            ) {
                tracing::warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    session_id = %session,
                    "web send: speaking lease renewal lost after acceptance; the message stays accepted"
                );
            }
        }
    }
    if let (Some(result), Some(admitted)) = (result_message, admission.as_ref()) {
        if let Err(error) = result.validate_admission(admitted, &decision) {
            // The managed admission already committed — the queue
            // finishes that work by itself. This operation's direct
            // runs, however, can never be delivered now; release
            // exactly them.
            flow.release_output_runs(&operation_id, &direct_send_ids)
                .await;
            return Err(error);
        }
    }
    if let Some(result) = admission.as_ref().filter(|r| r.duplicate) {
        // A duplicate never executes the fresh pre-assigned runs —
        // release the whole freshly-registered set so the retried
        // operation never holds open output it cannot produce.
        flow.release_output_runs(&operation_id, &operation_run_ids)
            .await;
        return Ok(WebSendOutcome {
            queue_admission: Some(result.into()),
            operation_id,
            primary_run_id: result.deliveries.iter().find_map(|d| d.run_id.clone()).unwrap_or_default(),
            status: "accepted".into(), active_run_ids: Vec::new(), bot_deliveries: Vec::new(),
            frontend_deliveries: Vec::new(), mentions: decision.mentions, hidden_mentions: decision.hidden_mentions,
            delivered_count: 0, failed_count: 0, delivery_results: Vec::new(),
        });
    }
    if let Some(admitted) = admission.as_ref() {
        // Terminal-at-commit deliveries (Failed/RejectedCapacity)
        // never run and never emit: release their pre-assigned
        // identities exactly — after the commit tells us which.
        let rejected: Vec<String> = admitted
            .deliveries
            .iter()
            .filter(|delivery| {
                matches!(
                    delivery.state.status,
                    bcs_domain::message_delivery::MessageDeliveryStatus::Failed
                        | bcs_domain::message_delivery::MessageDeliveryStatus::RejectedCapacity
                )
            })
            .filter_map(|delivery| delivery.run_id.clone())
            .collect();
        if !rejected.is_empty() {
            flow.release_output_runs(&operation_id, &rejected).await;
        }
    }
    if admission.is_none() {
        if let Some(result) = result_message {
            result.persist(flow).await?;
        } else {
            try_persist_group_message(
                flow,
                &cmd.group_id,
                cmd.session_id.as_deref(),
                &cmd.from_actor_id,
                sender_type,
                "chat",
                persisted_inbound_content(&cmd.message, cmd.attachments.as_deref(), &cmd.mentions),
                cmd.idempotency_key.as_deref(),
                None,
                "", // run_id: user messages don't associate with bot runs
            )
            .await?;
        }
    }
    // Notify @-mentioned humans only after the message is accepted and
    // persisted, and only if the content passes the outbound policy that
    // governs bot deliveries of the same message.
    if let Some(mention_actor_ids) = mention_source {
        if let Some(notify_text) = apply_notify_outbound_policy(
            flow,
            &cmd.group_id,
            &cmd.from_actor_id,
            &mention_message_text,
            &decision.targets,
        )
        .await
        {
            crate::human_notify_hook::spawn_human_mention_notify(
                flow.group.as_ref(),
                &flow.human_mention_notify,
                &flow.session_management,
                Some(mention_actor_ids),
                &overlay,
                crate::human_notify_hook::MentionNotifyContext {
                    session_id: cmd.session_id.clone().unwrap_or_default(),
                    group_id: cmd.group_id.clone(),
                    group_name: group.label.clone(),
                    sender_actor_id: cmd.from_actor_id.clone(),
                    sender_label: sender_display_name.clone(),
                    message_text: notify_text,
                    timestamp_ms: now_ms(),
                },
            )
            .await;
        }
    }
    let mut active_run_ids = Vec::new();
    let mut bot_deliveries = Vec::new();
    let mut delivery_results = Vec::new();

    // The send's OWN planned Send runs (managed runs stay out: they
    // execute independently of this phase). Each leaves the
    // unsettled set as ITS outcome is decided; a run that is still
    // unsettled when an abort happens is provably output-less and
    // gets explicitly released — runs that already delivered (or
    // whose transport outcome is uncertain) keep their operation
    // until their own terminal.
    let mut unsettled_send_ids = direct_send_ids;

    for plan in plans {
        let (run_id, target_bot_id, delivery_type, delivery_target, mut frame) = match plan {
            TargetPlan::Rejected { summary, delivery } => {
                delivery_results.push(summary);
                bot_deliveries.push(delivery);
                continue;
            }
            TargetPlan::Deliver {
                run_id,
                target_bot_id,
                delivery_type,
                delivery_target,
                frame,
            } => (run_id, target_bot_id, delivery_type, delivery_target, frame),
        };
        if let Some(scope) = crate::caller_execution::ExecutionRef::from_command(&cmd) {
            if let Err(error) = crate::caller_execution::apply(flow, &scope, &delivery_target, &target_bot_id,
                &cmd.group_id, cmd.session_id.as_deref().unwrap_or_default(),
                cmd.source_im_message_id.as_deref(), &mut frame).await {
                flow.release_output_runs(&operation_id, &unsettled_send_ids).await;
                return Err(error);
            }
        }
        let delivery_kind = bot_delivery_kind(delivery_type);
        let source_im_message_id = cmd.source_im_message_id.as_deref().filter(|message_id| {
            delivery_type == DeliveryType::Send && !message_id.trim().is_empty()
        });
        if let Some(message_id) = source_im_message_id {
            // Cache before delivery: a fast bot may emit its first response
            // before the delivery call itself has returned.
            flow.message_tracker
                .cache_channel_source_message_id(&run_id, message_id)
                .await;
        }
        // Register before transport delivery so an immediate chat.abort can
        // discover the run even before the Bot acknowledges chat.send.
        // A failure here aborts the send: THIS run never registered
        // and no later plan starts — release exactly the still
        // unsettled runs (never the already-delivered siblings,
        // which are live output).
        if let Err(error) = flow
            .register_send_context(
                delivery_type,
                &delivery_target,
                &frame,
                &run_id,
                &target_bot_id,
                &cmd.group_id,
                cmd.session_id.as_deref(),
                &cmd.provider_bypass_headers,
            )
            .await
        {
            flow.release_output_runs(&operation_id, &unsettled_send_ids)
                .await;
            return Err(error);
        }
        let delivery = flow
            .bot_delivery
            .deliver(BotDeliveryCommand {
                target: delivery_target,
                run_id: run_id.clone(),
                frame,
                delivery_kind,
                provider_transport: Default::default(),
                provider_bypass_headers: cmd.provider_bypass_headers.clone(),
            })
            .await;

        match delivery {
            Ok(result) => {
                log_bot_deliver_result(
                    &cmd.group_id,
                    cmd.session_id.as_deref(),
                    &run_id,
                    &target_bot_id,
                    delivery_type,
                    result.delivered,
                    result.error.as_ref().map(ToString::to_string).as_deref(),
                    None,
                );
                unsettled_send_ids.retain(|candidate| candidate != &run_id);
                if delivery_type == DeliveryType::Send && result.delivered {
                    active_run_ids.push(run_id.clone());
                } else {
                    flow.discard_send_context(&run_id).await?;
                    // An explicit downstream rejection (delivered =
                    // false is a definitive outcome, never transport
                    // uncertainty): this run produces nothing —
                    // release it explicitly.
                    if delivery_type == DeliveryType::Send {
                        flow.release_output_runs(&operation_id, &[run_id.clone()])
                            .await;
                    }
                }
                if !result.delivered && source_im_message_id.is_some() {
                    flow.message_tracker
                        .remove_channel_source_message_id(&run_id)
                        .await;
                }
                delivery_results.push(delivery_result_summary(
                    &target_bot_id,
                    delivery_type,
                    result.delivered,
                    result.error.as_ref().map(ToString::to_string),
                ));
                bot_deliveries.push(result);
            }
            Err(error) => {
                // Keep the ingress context, active index and channel source:
                // a callback must still pass identity/deadline checks and route
                // its reply even when the transport acknowledgement was lost.
                // A transport Err is UNCERTAIN, not terminal: the
                // remote may have accepted the run. Its callback
                // events still resolve this operation (uncertainty
                // failure keeps the binding) — release needs an
                // explicit proof of no-output, which this is not.
                unsettled_send_ids.retain(|candidate| candidate != &run_id);
                let error_text = error.to_string();
                log_bot_deliver_result(
                    &cmd.group_id,
                    cmd.session_id.as_deref(),
                    &run_id,
                    &target_bot_id,
                    delivery_type,
                    false,
                    Some(error_text.as_str()),
                    Some("deliver"),
                );
                delivery_results.push(delivery_result_summary(
                    &target_bot_id,
                    delivery_type,
                    false,
                    Some(error_text),
                ));
                bot_deliveries.push(BotDeliveryResult {
                    target_bot_id,
                    delivered: false,
                    error: Some(error),
                });
            }
        }
    }
    let frontend_deliveries = publish_web_user_message(flow, &cmd, &operation_id).await;

    // Notify when @-mentioned (Send) bots failed delivery. A failed delivery
    // is reported as "已离线" when the bot is genuinely unreachable
    // (is_available=false / no resolvable delivery target); when the bot is
    // still online (is_available=true) but the delivery itself failed (e.g. a
    // transient HTTP provider webhook error), it is reported as a retryable
    // delivery failure instead, so a still-active bot is not wrongly shown as
    // offline.
    {
        let failed_targets: Vec<&RoutingTarget> = decision
            .targets
            .iter()
            .filter(|t| t.delivery_type == DeliveryType::Send)
            .filter(|t| {
                bot_deliveries
                    .iter()
                    .any(|r| r.target_bot_id == t.bot_uuid && !r.delivered)
            })
            .collect();

        let mut offline_bot_names: Vec<String> = Vec::new();
        let mut delivery_failed = false;
        for t in &failed_targets {
            let name = group
                .get_participant(&t.bot_uuid)
                .and_then(|p| p.bot_name.clone())
                .unwrap_or_else(|| t.bot_uuid.clone());
            let is_online = match flow.registry.resolve_delivery_target(&t.bot_uuid).await {
                Ok(target) => flow.bot_delivery.is_available(&target).await,
                Err(_) => false,
            };
            if is_online {
                delivery_failed = true;
            } else {
                offline_bot_names.push(name);
            }
        }

        if let Some(ref system_message) = flow.system_message {
            let session_id = cmd.session_id.as_deref().unwrap_or(&cmd.group_id);
            let receivers: Vec<Participant> = group
                .participants
                .iter()
                .filter(|p| p.is_bot() && p.bot_uuid != cmd.from_actor_id)
                .cloned()
                .collect();

            if !offline_bot_names.is_empty() {
                let names = offline_bot_names.join("、");
                let message = format!("Bot {} 已离线", names);
                let event = SystemMessageEvent::GenericNotification {
                    group_id: cmd.group_id.clone(),
                    message,
                    receivers: receivers.clone(),
                };
                let _ = system_message
                    .notify(&cmd.group_id, event, session_id, &group.participants)
                    .await;
            }

            if delivery_failed {
                let event = SystemMessageEvent::GenericNotification {
                    group_id: cmd.group_id.clone(),
                    message: "消息投递失败，请稍后重试。".to_string(),
                    receivers,
                };
                let _ = system_message
                    .notify(&cmd.group_id, event, session_id, &group.participants)
                    .await;
            }
        }
    }

    if !decision.hidden_mentions.is_empty() {
        if let Some(ref system_message) = flow.system_message {
            let session_id = cmd.session_id.as_deref().unwrap_or(&cmd.group_id);
            for hidden in &decision.hidden_mentions {
                let event = SystemMessageEvent::BotHiddenNotice {
                    group_id: cmd.group_id.clone(),
                    mentioner_bot_id: cmd.from_actor_id.clone(),
                    hidden_bot_name: hidden.hidden_bot_name.clone(),
                };
                let _ = system_message
                    .notify(&cmd.group_id, event, session_id, &group.participants)
                    .await;
            }
        }
    }

    // Notify when @-mentioned bots are muted (downgraded Send→Inject).
    // Aggregates multiple muted bots into a single system message.
    {
        let muted_bot_names: Vec<String> = decision
            .targets
            .iter()
            .filter(|t| {
                t.delivery_type == DeliveryType::Inject && decision.mentions.contains(&t.bot_uuid)
            })
            .filter_map(|t| {
                overlay
                    .iter()
                    .find(|o| o.bot_uuid == t.bot_uuid)
                    .and_then(|row| {
                        let effective_mode = row
                            .mode
                            .unwrap_or_else(|| ParticipantMode::default_for(row.actor_kind));
                        if effective_mode == ParticipantMode::Muted
                            && row.status != ActorStatus::Hidden
                        {
                            Some(
                                row.bot_name
                                    .clone()
                                    .or_else(|| {
                                        group
                                            .get_participant(&t.bot_uuid)
                                            .and_then(|p| p.bot_name.clone())
                                    })
                                    .unwrap_or_else(|| t.bot_uuid.clone()),
                            )
                        } else {
                            None
                        }
                    })
            })
            .collect();

        if !muted_bot_names.is_empty() {
            if let Some(ref system_message) = flow.system_message {
                let session_id = cmd.session_id.as_deref().unwrap_or(&cmd.group_id);
                let names = muted_bot_names.join("、");
                let message = format!("Bot {} 已切换成禁言模式", names);
                let receivers: Vec<Participant> = group
                    .participants
                    .iter()
                    .filter(|p| p.is_bot() && p.bot_uuid != cmd.from_actor_id)
                    .cloned()
                    .collect();
                let event = SystemMessageEvent::GenericNotification {
                    group_id: cmd.group_id.clone(),
                    message,
                    receivers,
                };
                let _ = system_message
                    .notify(&cmd.group_id, event, session_id, &group.participants)
                    .await;
            }
        }
    }

    let primary_run_id = active_run_ids
        .first()
        .cloned()
        .or_else(|| admission.as_ref().and_then(|a| a.deliveries.iter().find_map(|d| d.run_id.clone())))
        .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());

    Ok(WebSendOutcome {
        queue_admission: admission.as_ref().map(Into::into),
        operation_id,
        primary_run_id,
        status: if active_run_ids.is_empty() {
            match &admission {
                Some(result) => queue_admission_status(result.deliveries.iter().map(|d| (d.state.kind, d.state.status))),
                None => "started",
            }
        } else { "started" }.to_string(),
        active_run_ids,
        bot_deliveries,
        frontend_deliveries,
        mentions: decision.mentions,
        hidden_mentions: decision.hidden_mentions,
        delivered_count: delivery_results
            .iter()
            .filter(|result| result.success)
            .count(),
        failed_count: delivery_results
            .iter()
            .filter(|result| !result.success)
            .count(),
        delivery_results,
    })
}

/// Observer context cannot make a rejected reply-producing Send look queued.
fn queue_admission_status(deliveries: impl Iterator<Item = (DeliveryType, bcs_domain::message_delivery::MessageDeliveryStatus)>) -> &'static str {
    use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
    let deliveries: Vec<_> = deliveries.collect();
    let sends: Vec<_> = deliveries.iter().filter(|(kind, _)| *kind == DeliveryType::Send).collect();
    let relevant: Vec<_> = if sends.is_empty() { deliveries.iter().collect() } else { sends };
    if !relevant.is_empty() && relevant.iter().all(|(_, status)| matches!(status, Status::Failed | Status::RejectedCapacity)) {
        return if relevant.iter().any(|(_, status)| *status == Status::Failed) { "failed" } else { "rejected_capacity" };
    }
    if !deliveries.is_empty() && deliveries.iter().all(|(kind, _)| *kind == DeliveryType::Inject) {
        "context_saved"
    } else {
        "queued"
    }
}

#[cfg(test)]
mod queue_admission_status_tests {
    use super::queue_admission_status;
    use bcs_domain::{DeliveryType::{Send, Inject}, message_delivery::MessageDeliveryStatus::{Failed, RejectedCapacity, Queued, PendingContext}};

    #[test]
    fn aggregates_reply_producing_targets_without_observer_masking() {
        for (deliveries, expected) in [
            (vec![(Send, Failed), (Inject, PendingContext)], "failed"),
            (vec![(Send, Failed), (Send, RejectedCapacity), (Inject, PendingContext)], "failed"),
            (vec![(Send, Failed), (Send, Failed)], "failed"),
            (vec![(Send, Failed), (Send, Queued)], "queued"),
            (vec![(Send, Queued), (Inject, Failed)], "queued"),
            (vec![(Send, Queued), (Inject, PendingContext)], "queued"),
            (vec![(Send, RejectedCapacity), (Inject, PendingContext)], "rejected_capacity"),
            (vec![(Send, RejectedCapacity), (Send, RejectedCapacity)], "rejected_capacity"),
            (vec![(Inject, PendingContext)], "context_saved"),
            (vec![(Inject, Failed)], "failed"),
            (vec![], "queued"),
        ] {
            assert_eq!(queue_admission_status(deliveries.clone().into_iter()), expected, "{deliveries:?}");
        }
    }
}

fn caller_admission_error(error: crate::caller_admission::CallerAdmissionError) -> ServiceError {
    match error {
        crate::caller_admission::CallerAdmissionError::TargetNotCallerEnabled => ServiceError::InvalidOperation {
            message: "caller target is not caller-enabled".to_string(),
            request_id: None,
        },
        crate::caller_admission::CallerAdmissionError::IdentityMismatch => ServiceError::InvalidOperation {
            message: "caller identity mismatch".to_string(),
            request_id: None,
        },
        crate::caller_admission::CallerAdmissionError::HandoffRefused => ServiceError::InternalError(
            "caller identity handoff refused the slot".to_string(),
        ),
        crate::caller_admission::CallerAdmissionError::Admission(text) => ServiceError::InternalError(
            format!("queue admission failed: {text}"),
        ),
    }
}

fn command_targets_first_bot(decision: &RoutingDecision) -> &str {
    decision
        .targets
        .first()
        .map(|t| t.bot_uuid.as_str())
        .unwrap_or_default()
}
