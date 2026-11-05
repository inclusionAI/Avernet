//! Caller inheritance is metadata on the existing task admission, not a
//! parent/child execution service. Task/run/attempt state remains Managed's.
use bcs_service_api::{BotEventCommand, CallerDelegationAssignment, ServiceError, ServiceResult};

/// Resolve the real parent, reserve a bounded invocation allowance and return
/// only non-secret metadata for the existing delivery's atomic admission.
/// A reservation is never refunded: an admission error may have committed.
/// Existing coordination intent/tool-call dedup owns replay handling.
pub(crate) async fn prepare_assignment(
    flow: &crate::BcsMessageFlow,
    command: &BotEventCommand,
    invocation: &str,
    target_bot: &str,
) -> ServiceResult<Option<CallerDelegationAssignment>> {
    let row = crate::queued_admission::find_managed_run(flow, command).await?;
    let now = chrono::Utc::now().timestamp_millis();
    let caller_required = row.as_ref().is_some_and(|row| {
        row.semantic_projection_json
            .get("caller_required")
            .and_then(|v| v.as_bool())
            == Some(true)
            || row
                .semantic_projection_json
                .get("caller_delegation")
                .is_some()
    });
    let parent_run = row
        .as_ref()
        .and_then(|row| row.run_id.as_deref())
        .unwrap_or(&command.run_id);
    let Some(parent_row) = row.as_ref() else {
        // Review-VII P1: no Managed row does not mean no parent. A WORKFLOW
        // node run keeps its own lifecycle (spec §8.5) and never material in
        // the Managed ledger — its facts are the runtime lineage the
        // orchestration dispatch registered plus the live run context the
        // collaboration runtime published. Resolve THOSE as the parent
        // execution; a stale/no-row LEGACY run with neither lineage nor
        // context stays an ordinary task (never mints delegation material).
        return prepare_workflow_parent_assignment(flow, command, parent_run, invocation, target_bot, now).await;
    };
    let parent_attempt = parent_row.attempt_no;
    let parent_bot_id = parent_row.target_bot_id.clone();
    if parent_attempt == 0 || parent_bot_id.trim().is_empty() {
        return Err(refused("parent execution attempt unavailable"));
    }
    let lineage = flow
        .caller_root_lineage
        .as_ref()
        .and_then(|index| index.current(parent_run, now));
    let Some(lineage) = lineage else {
        // The durable delivery classification is not identity material. It
        // prevents restart/owner transfer from downgrading a lost identity.
        if caller_required {
            return Err(refused("caller lineage unavailable"));
        }
        // No marks, no lineage — fall through to the session-capture
        // fallback (spec L62): an ordinary master whose run lacks the
        // protected-dispatch marks can still mint identity material from
        // the session's relay capture.
        return prepare_session_capture_fallback(flow, command, invocation, now).await;
    };
    let context = match &flow.bot_run_context {
        Some(port) => port.get_context(&command.run_id).await,
        None => None,
    }
    .ok_or_else(|| refused("parent execution context unavailable"))?;
    let now = chrono::Utc::now().timestamp_millis();
    validate_parent(&context, &lineage, command, now)?;
    if !matches!(
        parent_row.state.status,
        bcs_domain::message_delivery::MessageDeliveryStatus::Dispatching
            | bcs_domain::message_delivery::MessageDeliveryStatus::Running
    ) {
        return Err(refused("parent delivery is not active"));
    }
    flow.caller_root_lineage
        .as_ref()
        .ok_or_else(|| refused("caller lineage unavailable"))?
        .reserve_invocation(parent_run, invocation, target_bot, now)?;
    Ok(Some(CallerDelegationAssignment {
        parent_run_id: parent_run.to_owned(),
        parent_attempt,
        parent_bot_id,
        invocation_id: invocation.to_string(),
    }))
}

/// Native requests have no invocation key on the wire. Once a start has been
/// claimed, require exact runtime material; never use session-capture fallback.
/// The actual Managed attempt (or workflow lineage) remains authoritative.
pub(crate) async fn prepare_native_assignment(
    flow: &crate::BcsMessageFlow, command: &BotEventCommand, invocation: &str,
    target_bot: &str, observed_attempt: u32, managed_parent: bool,
) -> ServiceResult<CallerDelegationAssignment> {
    // The event boundary has already canonicalized aliases. Use the exact
    // scoped lookup (LIMIT 2), not the legacy run/request/downstream UNION.
    let rows = match flow.managed_deliveries.as_ref() {
        Some(service) => service.lookup(bcs_service_api::port::repo::message_delivery::DeliveryLookup::CanonicalRun {
            bot: command.bot_id.clone(), session: command.bcs_session_id.clone().unwrap_or_default(),
            run: command.run_id.clone(),
        }).await.map_err(|_| refused("parent delivery lookup failed"))?,
        None => Vec::new(),
    };
    if rows.len() > 1 || (managed_parent && rows.is_empty()) {
        return Err(refused("parent delivery disappeared or is ambiguous"));
    }
    let context = match &flow.bot_run_context {
        Some(port) => port.get_context(&command.run_id).await,
        None => None,
    }.ok_or_else(|| refused("parent execution context unavailable"))?;
    // Refresh material and time after external reads; never reserve a stale snapshot.
    let now = chrono::Utc::now().timestamp_millis();
    let index = flow.caller_root_lineage.as_ref().ok_or_else(|| refused("caller lineage unavailable"))?;
    let lineage = index.current(&command.run_id, now).ok_or_else(|| refused("caller lineage unavailable"))?;
    validate_parent(&context, &lineage, command, now)?;
    if observed_attempt == 0 || lineage.attempt != observed_attempt
        || lineage.environment != bcs_config::resolve_env_str()
        || context.run_id != command.run_id {
        return Err(refused("parent execution attempt or environment mismatch"));
    }
    if let Some(row) = rows.first() {
        if row.group_id != command.group_id || row.attempt_no != observed_attempt
            || row.run_id.as_deref() != Some(command.run_id.as_str())
            || !matches!(row.state.status,
                bcs_domain::message_delivery::MessageDeliveryStatus::Dispatching
                | bcs_domain::message_delivery::MessageDeliveryStatus::Running) {
            return Err(refused("parent delivery is not active or attempt changed"));
        }
        // A durable Caller classification must not be downgraded to Human
        // origin merely because execution material was lost.
        if row.semantic_projection_json["caller_required"] == true
            && row.semantic_projection_json["caller_delegation"]["execution_purpose"] != "delegation_only"
            && !lineage.parent_execution {
            return Err(refused("parent execution material unavailable"));
        }
    }
    if lineage.parent_execution && index.current_parent_execution(
        &lineage.environment, Some(&lineage.group_id), &lineage.session_id,
        &lineage.root_run_id, &lineage.message_turn_id, &command.bot_id,
        &command.run_id, observed_attempt, now,
    ).is_none() {
        return Err(refused("parent execution material unavailable"));
    }
    index.reserve_invocation(&command.run_id, invocation, target_bot, now)?;
    Ok(CallerDelegationAssignment {
        parent_run_id: command.run_id.clone(), parent_attempt: observed_attempt,
        parent_bot_id: command.bot_id.clone(), invocation_id: invocation.into(),
    })
}

/// The per-target issuance purpose for a derived (delegated) mark (spec
/// §8.3, review P1-2 — the SAME per-target control-plane read the web_send
/// admission performs): Caller-enabled target → `execution`; absent or
/// disabled config → `delegation_only` middle hop. Never blanket—one way
/// or the other. Read failures resolve `delegation_only` (fail-closed on
/// the middle-hop side: no execution identity is minted from an unreadable
/// configuration).
pub(crate) async fn target_issuance_purpose(flow: &crate::BcsMessageFlow, target_bot_id: &str) -> &'static str {
    let target_caller_enabled = match flow.caller_admission.as_ref() {
        Some(deps) => deps
            .control_plane
            .get_caller_configuration(target_bot_id, &bcs_config::resolve_env_str())
            .await
            .ok()
            .flatten()
            .and_then(|config| config.snapshot)
            .is_some_and(|snapshot| snapshot.caller_identity.enabled),
        None => false,
    };
    if target_caller_enabled { "execution" } else { "delegation_only" }
}

fn validate_parent(
    context: &bcs_service_api::port::chat_run::BotRunContext,
    lineage: &crate::caller_lineage::RootLineage,
    command: &BotEventCommand,
    now: i64,
) -> ServiceResult<()> {
    if context.terminal
        || context.deadline_ms <= now as u64
        || lineage.expires_at_ms <= now
        || context.bot_id != command.bot_id
        || context.group_id != command.group_id
        || lineage.group_id != command.group_id
        || context.bcs_session_id.as_deref() != Some(lineage.session_id.as_str())
        || command.bcs_session_id.as_deref() != Some(lineage.session_id.as_str())
    {
        return Err(refused("parent execution scope or lifetime mismatch"));
    }
    Ok(())
}

fn refused(message: &str) -> ServiceError {
    ServiceError::InvalidOperation {
        message: message.into(),
        request_id: None,
    }
}

/// Review-VII P1: resolve a WORKFLOW node run's assignment facts from the
/// two trusted materials message-flow owns — the runtime lineage registered
/// at the orchestration dispatch (§15.89 S1: depth = instance+1, attempt =
/// the node's own attempt, scoped group/session/env, the fixed instance
/// cutoff and window) and the live `BotRunContext` the collaboration runtime
/// published for the node run (bot/group/session/deadline/terminal). No
/// Managed row is fabricated and none is consulted: the Workflow's own
/// execution records stay authoritative.
///
/// The validation is the same shape as the Managed path's `validate_parent`:
/// liveness (lineage window + context deadline + non-terminal), identity
/// binding (the context's bot IS the echo's bot), and scope equality with
/// the command and the lineage. The parent attempt is the node lineage's
/// recorded attempt — the exact `Material Ledger` key the delegate
/// dispatch validates against (`lineage.attempt == facts.parent_attempt`).
async fn prepare_workflow_parent_assignment(
    flow: &crate::BcsMessageFlow,
    command: &BotEventCommand,
    parent_run: &str,
    invocation: &str,
    target_bot: &str,
    now: i64,
) -> ServiceResult<Option<CallerDelegationAssignment>> {
    let Some(lineage) = flow
        .caller_root_lineage
        .as_ref()
        .and_then(|index| index.current(parent_run, now))
    else {
        // No runtime lineage: fall through to the session-capture fallback
        // (spec L62) before settling on an ordinary task. The relay capture
        // alone is sufficient identity material — the human spoke in this
        // session, and the relay ledger redeems at dispatch time.
        return prepare_session_capture_fallback(flow, command, invocation, now).await;
    };
    let context = match &flow.bot_run_context {
        Some(port) => port.get_context(&command.run_id).await,
        None => None,
    };
    let Some(context) = context else {
        // A lineage without its live execution context cannot prove the
        // parent is alive and bound to this echo — refuse closed rather
        // than minting delegation facts on lineage alone.
        return Err(refused("parent execution context unavailable"));
    };
    if context.terminal
        || context.deadline_ms <= now as u64
        || lineage.expires_at_ms <= now
        || context.bot_id != command.bot_id
        || context.group_id != command.group_id
        || lineage.group_id != command.group_id
        || context.bcs_session_id.as_deref() != Some(lineage.session_id.as_str())
        || command.bcs_session_id.as_deref() != Some(lineage.session_id.as_str())
    {
        return Err(refused("parent execution scope or lifetime mismatch"));
    }
    let parent_attempt = lineage.attempt;
    let parent_bot_id = context.bot_id.clone();
    if parent_attempt == 0 || parent_bot_id.trim().is_empty() {
        return Err(refused("parent execution attempt unavailable"));
    }
    // The SAME replay-dedup ownership as the Managed path: the trusted
    // caller's invocation marker reserves the allowance on the node's turn
    // budget (§8.5: fan-out nodes share it); replays of the same echo
    // reclaim nothing new.
    flow.caller_root_lineage
        .as_ref()
        .ok_or_else(|| refused("caller lineage unavailable"))?
        .reserve_invocation(parent_run, invocation, target_bot, now)?;
    Ok(Some(CallerDelegationAssignment {
        parent_run_id: parent_run.to_owned(),
        parent_attempt,
        parent_bot_id,
        invocation_id: invocation.to_string(),
    }))
}

/// Spec L62 (relay-as-identity-source): fallback when the managed row
/// lacks `caller_required` marks and no orchestration lineage exists
/// (an ordinary master, not caller-ON), but the session holds a live
/// owner→caller relay capture. The identity material comes from the
/// relay ledger's per-turn bounded redemption AT DISPATCH TIME (the
/// dispatch already checks its own budget), not from the managed row.
///
/// This supports "master in owner mode (not caller-enabled), worker
/// caller-enabled" chains: the human identification is keyed to the
/// session (the human spoke here), not to the master's protocol entry
/// point, so any bot dispatching tasks within this session can carry
/// the identity as long as the relay is assembled and the capture is
/// live. This is additive — all existing paths are unchanged.
async fn prepare_session_capture_fallback(
    flow: &crate::BcsMessageFlow,
    command: &BotEventCommand,
    invocation: &str,
    now: i64,
) -> ServiceResult<Option<CallerDelegationAssignment>> {
    // Gate 1: relay assembly AND caller admission deps must both be wired.
    // The identity inheritance is end-to-end only when the composition can
    // actually park the redeemed capture on the worker's handoff scope
    // (the dispatch relay block requires caller_admission to redeem); a
    // relay-only wiring (e.g. the chat-final contract harness) would mint
    // delegation material the dispatch cannot make good on, silently
    // diverging task delivery.
    let Some(relay) = flow.caller_relay.as_ref() else {
        return Ok(None);
    };
    if flow.caller_admission.is_none() {
        return Ok(None);
    }
    // Gate 2: the session must have a live owner identity capture (the
    // human spoke in this session — the identity is theirs, not the
    // master's; `has_live_capture` is non-consuming).
    let Some(session_id) = command
        .bcs_session_id
        .as_deref()
        .filter(|id| !id.is_empty())
    else {
        return Ok(None);
    };
    if !relay
        .ledger
        .has_live_capture(&bcs_config::resolve_env_str(), session_id, now)
    {
        return Ok(None);
    }
    // Gate 3: the dispatching master's execution context must still be
    // alive — a terminal or expired run must not mint identity material.
    let context = match &flow.bot_run_context {
        Some(port) => port.get_context(&command.run_id).await,
        None => None,
    };
    match context {
        Some(context) => {
            if context.terminal
                || context.deadline_ms <= now as u64
                || context.bot_id != command.bot_id
                || context.group_id != command.group_id
            {
                return Ok(None);
            }
        }
        None => return Ok(None),
    }
    // Live capture + live run: mint the delegation. The identity itself
    // flows from the relay ledger at dispatch time (budget per capture:
    // RELAY_PER_TURN redeems, expiry-bound). We mint only the non-secret
    // association metadata here (spec L62: identity material never travels
    // through the middle bot).
    tracing::debug!(
        bot_id = %command.bot_id,
        group_id = %command.group_id,
        run_id = %command.run_id,
        session_id = %session_id,
        "audit: session-capture delegation fallback fired (spec L62)"
    );
    Ok(Some(CallerDelegationAssignment {
        parent_run_id: command.run_id.to_string(),
        parent_attempt: 1,
        parent_bot_id: command.bot_id.clone(),
        invocation_id: invocation.to_string(),
    }))
}

#[cfg(test)]
#[path = "caller_delegation_tests.rs"]
mod tests;

/// Legacy direct Send cannot provide Caller execution. Configuration errors
/// must not silently select Owner; queued protected delivery is the only path.
pub(crate) async fn guard_legacy_relay_send(
    flow: &crate::BcsMessageFlow, target_bot_id: &str,
) -> ServiceResult<()> {
    let Some(deps) = flow.caller_admission.as_ref() else { return Ok(()); };
    let config = deps.control_plane
        .get_caller_configuration(target_bot_id, &bcs_config::resolve_env_str()).await?;
    if config.and_then(|config| config.snapshot)
        .is_some_and(|snapshot| snapshot.caller_identity.enabled)
    {
        return Err(refused("caller target requires protected queued delivery; legacy relay refused"));
    }
    Ok(())
}
