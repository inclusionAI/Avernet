use super::*;

// ---------------------------------------------------------------------------
// SSE read loop (2.0 downlink streaming)
// ---------------------------------------------------------------------------

/// Two ASCII bytes `\n\n` (LF LF) — a frame separator.
const FRAME_SEP_LF: &[u8] = b"\n\n";
/// Four ASCII bytes `\r\n\r\n` (CRLF CRLF) — the other frame separator.
const FRAME_SEP_CRLF: &[u8] = b"\r\n\r\n";

/// Find the earliest frame separator (`\n\n` or `\r\n\r\n`) in `buf`.
/// Returns `(start_index, separator_len)` of the match closest to the front.
pub(super) fn find_frame_sep(buf: &[u8]) -> Option<(usize, usize)> {
    let lf = find_subslice(buf, FRAME_SEP_LF);
    let crlf = find_subslice(buf, FRAME_SEP_CRLF);
    match (lf, crlf) {
        (Some(a), Some(b)) => {
            if a <= b {
                Some((a, FRAME_SEP_LF.len()))
            } else {
                Some((b, FRAME_SEP_CRLF.len()))
            }
        }
        (Some(a), None) => Some((a, FRAME_SEP_LF.len())),
        (None, Some(b)) => Some((b, FRAME_SEP_CRLF.len())),
        (None, None) => None,
    }
}

pub(super) fn find_subslice(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() || haystack.len() < needle.len() {
        return None;
    }
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

/// Read the SSE byte stream from `resp`, split into frames, parse, dedupe by
/// `StreamEvent.seq`, and ingest into the message-flow pipeline. Closes the run
/// with a synthesized error terminal on idle timeout, read error, or a stream
/// that ends without a chat terminal (#3). Resolves run context (with bounded
/// retry for the put_context race, #2) before ingesting any frame.
pub(super) async fn stream_and_drive(
    resp: reqwest::Response,
    bcn_run_id: String,
    bot_id: String,
    flow: Arc<dyn ProviderEventIngestService>,
    ctx: Arc<dyn BotRunContextPort>,
    interaction_context: Option<SseInteractionContext>,
) {
    use futures::StreamExt;

    // Resolve run context first (group_id needed for every payload). Retry to
    // cover the put_context race: deliver() returns -> put_context runs, but the
    // spawned reader may reach here first.
    let Some(run_ctx) = resolve_run_context(&ctx, &bcn_run_id).await else {
        warn!(run_id = %bcn_run_id, "sse: run context never became available; closing");
        return;
    };
    let group_id = run_ctx.group_id.clone();
    let bcs_session_id = run_ctx.bcs_session_id.clone();
    // #2: stash the run deadline so every frame can be guarded against an
    // already-expired run before it is ingested (re-checked per frame below).
    let deadline_ms = run_ctx.deadline_ms;

    let mut stream = resp.bytes_stream();
    let mut buf: Vec<u8> = Vec::new();
    let mut dedup = SeqDedup::default();
    let mut lag = LagTracker::default();
    let mut saw_terminal = false;
    let mut close_reason = "run_terminal";

    // SSE-detail diagnostics: how many frames we consumed, when we started, and
    // how long we blocked waiting on the socket vs processing frames. The gap
    // between `stream.next()` returns (idle waits) and total elapsed tells us
    // whether a stall (idle timeout) or a truncated/reset stream closed the run.
    let started_ms = bcs_protocol::now_ms();
    let mut frames: u64 = 0;
    tracing::info!(
        target: "bcs_sse_detail",
        run_id = %bcn_run_id,
        group_id = %group_id,
        started_ms,
        idle_timeout_ms = SSE_IDLE_TIMEOUT_MS,
        deadline_ms,
        "sse begin reading downlink stream"
    );

    'read: loop {
        let now_ms = bcs_protocol::now_ms();
        let (read_timeout, deadline_limited) = sse_next_read_timeout(now_ms, deadline_ms);
        if deadline_limited && read_timeout.is_zero() {
            close_reason = "run_deadline";
            warn!(run_id = %bcn_run_id, "sse run deadline reached; closing stream");
            break 'read;
        }
        let next = tokio::time::timeout(read_timeout, stream.next()).await;
        match next {
            Err(_) => {
                if deadline_limited {
                    close_reason = "run_deadline";
                }
                warn!(
                    target: "bcs_sse_detail",
                    run_id = %bcn_run_id,
                    frames,
                    saw_terminal,
                    elapsed_ms = bcs_protocol::now_ms().saturating_sub(started_ms),
                    deadline_limited,
                    "sse read timeout; closing run as error"
                );
                break 'read;
            }
            Ok(None) => {
                // Stream ended. Flush any trailing buffered (non-empty) frame.
                if !buf.is_empty() {
                    if buf.len() > SSE_MAX_FRAME_BYTES {
                        warn!(
                            run_id = %bcn_run_id,
                            frame_bytes = buf.len(),
                            max_frame_bytes = SSE_MAX_FRAME_BYTES,
                            "oversized trailing SSE frame; closing run"
                        );
                        break 'read;
                    }
                    frames += 1;
                    if let Some(done) = drive_frame_bytes(
                        &buf,
                        &bcn_run_id,
                        &group_id,
                        &bot_id,
                        &bcs_session_id,
                        deadline_ms,
                        &mut dedup,
                        &mut lag,
                        &flow,
                        interaction_context.as_ref(),
                    )
                    .await
                    {
                        saw_terminal = done;
                    }
                }
                tracing::info!(
                    target: "bcs_sse_detail",
                    run_id = %bcn_run_id,
                    frames,
                    saw_terminal,
                    elapsed_ms = bcs_protocol::now_ms().saturating_sub(started_ms),
                    "sse stream ended (EOF)"
                );
                break 'read;
            }
            Ok(Some(Err(error))) => {
                warn!(
                    target: "bcs_sse_detail",
                    run_id = %bcn_run_id,
                    %error,
                    frames,
                    saw_terminal,
                    elapsed_ms = bcs_protocol::now_ms().saturating_sub(started_ms),
                    "sse read error; closing run as error"
                );
                break 'read;
            }
            Ok(Some(Ok(chunk))) => {
                buf.extend_from_slice(&chunk);
                while let Some((idx, sep_len)) = find_frame_sep(&buf) {
                    let frame: Vec<u8> = buf.drain(..idx + sep_len).collect();
                    if frame.len() > SSE_MAX_FRAME_BYTES {
                        warn!(
                            run_id = %bcn_run_id,
                            frame_bytes = frame.len(),
                            max_frame_bytes = SSE_MAX_FRAME_BYTES,
                            "oversized SSE frame; closing run"
                        );
                        break 'read;
                    }
                    frames += 1;
                    if let Some(done) = drive_frame_bytes(
                        &frame,
                        &bcn_run_id,
                        &group_id,
                        &bot_id,
                        &bcs_session_id,
                        deadline_ms,
                        &mut dedup,
                        &mut lag,
                        &flow,
                        interaction_context.as_ref(),
                    )
                    .await
                    {
                        if done {
                            saw_terminal = true;
                            break 'read;
                        }
                    }
                }
                if buf.len() > SSE_MAX_FRAME_BYTES {
                    warn!(
                        run_id = %bcn_run_id,
                        buffered_bytes = buf.len(),
                        max_frame_bytes = SSE_MAX_FRAME_BYTES,
                        "unterminated SSE frame exceeded buffer limit; closing run"
                    );
                    break 'read;
                }
            }
        }
    }

    // #3: any close path that did NOT see a chat terminal synthesizes one so the
    // run is cleanly closed and the frontend is never left hanging.
    if !saw_terminal {
        warn!(
            target: "bcs_sse_detail",
            run_id = %bcn_run_id,
            frames,
            elapsed_ms = bcs_protocol::now_ms().saturating_sub(started_ms),
            "sse closed without chat terminal; synthesizing error terminal"
        );
        ingest_synthesized_error(&bcn_run_id, &group_id, &bot_id, &bcs_session_id, &flow).await;
    }
    // #2: mark the run terminal in the run-context store after closing.
    ctx.mark_terminal(&bcn_run_id).await;
    ctx.mark_provider_transport_terminal(&bcn_run_id).await;
    if let Some(interactions) = interaction_context {
        if let Err(error) = interactions
            .service
            .invalidate_run(&bcn_run_id, close_reason, bcs_protocol::now_ms())
            .await
        {
            warn!(run_id = %bcn_run_id, %error, "failed to invalidate run interactions");
        }
    }
}

/// Resolve the run context for `run_id`, retrying a bounded number of times to
/// cover the put_context race (#2). Returns `None` once retries are exhausted.
pub(super) async fn resolve_run_context(
    ctx: &Arc<dyn BotRunContextPort>,
    run_id: &str,
) -> Option<BotRunContext> {
    for _ in 0..SSE_CTX_RETRY_MAX {
        if let Some(found) = ctx.get_context(run_id).await {
            return Some(found);
        }
        tokio::time::sleep(Duration::from_millis(SSE_CTX_RETRY_INTERVAL_MS)).await;
    }
    ctx.get_context(run_id).await
}

/// Decode one complete frame's bytes, parse + classify + dedupe + ingest.
/// Returns `Some(true)` if this frame closed the run (terminal), `Some(false)`
/// if a non-terminal event was ingested, and `None` if the frame was dropped
/// (ping / unknown / duplicate / not-json / no run context).
pub(super) async fn drive_frame_bytes(
    frame_bytes: &[u8],
    bcn_run_id: &str,
    group_id: &str,
    bot_id: &str,
    bcs_session_id: &Option<String>,
    deadline_ms: u64,
    dedup: &mut SeqDedup,
    lag: &mut LagTracker,
    flow: &Arc<dyn ProviderEventIngestService>,
    interaction_context: Option<&SseInteractionContext>,
) -> Option<bool> {
    // Decode only the complete frame bytes (#5). Lossy + WARN on invalid UTF-8.
    let text = match std::str::from_utf8(frame_bytes) {
        Ok(text) => text.to_string(),
        Err(error) => {
            warn!(run_id = %bcn_run_id, %error, "sse frame not valid utf-8; lossy-decoding");
            String::from_utf8_lossy(frame_bytes).into_owned()
        }
    };
    if text.trim().is_empty() {
        return None;
    }
    drive_sse_frame(
        &text,
        bcn_run_id,
        group_id,
        bot_id,
        bcs_session_id,
        deadline_ms,
        dedup,
        lag,
        flow,
        interaction_context,
    )
    .await
}

/// Core per-frame ingest logic, decoupled from the byte source so tests can
/// drive it from in-memory text. Returns the same tri-state as
/// `drive_frame_bytes`.
pub(super) async fn drive_sse_frame(
    block: &str,
    bcn_run_id: &str,
    group_id: &str,
    bot_id: &str,
    bcs_session_id: &Option<String>,
    deadline_ms: u64,
    dedup: &mut SeqDedup,
    lag: &mut LagTracker,
    flow: &Arc<dyn ProviderEventIngestService>,
    interaction_context: Option<&SseInteractionContext>,
) -> Option<bool> {
    let frame = parse_sse_block(block)?;
    let data: Value = match serde_json::from_str(&frame.data) {
        Ok(value) => value,
        Err(error) => {
            warn!(
                target: "bcs_sse_detail",
                run_id = %bcn_run_id,
                event = %frame.event,
                %error,
                sse_data = %sse_data_log(&frame.event, &frame.data),
                "sse data not json; dropping frame"
            );
            warn!(run_id = %bcn_run_id, %error, "sse data not json; dropping frame");
            return None;
        }
    };
    let event = parse_stream_event(&frame.event, data);
    let kind = classify(&event);
    let recv_ms = bcs_protocol::now_ms();

    // SSE-detail per-frame trace: `lag_ms` (receipt time minus the engine
    // frame's own ts) measures how far BCS consumption trails the producer.
    // Goes only to the bcs-sse-detail.log target. Interaction business payloads
    // are reduced to safe correlation metadata before they reach that log.
    {
        let frame_ts = stream_event_ts(&event);
        let seq = stream_event_seq(&event).unwrap_or(0);
        let lag_ms = frame_ts.map(|t| recv_ms.saturating_sub(t)).unwrap_or(0);
        let state = ingest_kind_state_slug(&kind);

        // Edge-triggered lag alert: WARN once when the run crosses the alert
        // threshold (falling behind the producer) and once when it recovers, so
        // a sustained backlog is visible without a per-frame WARN flood.
        if lag_ms > SSE_LAG_ALERT_MS {
            lag.peak_lag_ms = lag.peak_lag_ms.max(lag_ms);
            if !lag.alerting {
                lag.alerting = true;
                warn!(
                    target: "bcs_sse_detail",
                    run_id = %bcn_run_id,
                    seq,
                    lag_ms,
                    alert_threshold_ms = SSE_LAG_ALERT_MS,
                    "sse consumption falling behind producer"
                );
            }
        } else if lag.alerting {
            lag.alerting = false;
            warn!(
                target: "bcs_sse_detail",
                run_id = %bcn_run_id,
                seq,
                lag_ms,
                peak_lag_ms = lag.peak_lag_ms,
                "sse consumption recovered"
            );
            lag.peak_lag_ms = 0;
        }

        tracing::info!(
            target: "bcs_sse_detail",
            run_id = %bcn_run_id,
            event = %frame.event,
            state = state,
            seq,
            frame_ts = frame_ts.unwrap_or(0),
            recv_ms,
            lag_ms,
            sse_data = %sse_data_log(&frame.event, &frame.data),
            "sse frame recv"
        );
    }

    if matches!(kind, IngestKind::Interaction) {
        match dedup.accept(stream_event_seq(&event)) {
            SeqDecision::Duplicate => {
                warn!(run_id = %bcn_run_id, "duplicate/regressed interaction seq; dropping");
                return None;
            }
            SeqDecision::Gap(gap) => {
                warn!(run_id = %bcn_run_id, gap, "seq gap before interaction")
            }
            SeqDecision::Accept => {}
        }
        if bcs_protocol::now_ms() > deadline_ms {
            warn!(run_id = %bcn_run_id, "run deadline exceeded; dropping interaction frame");
            if let Some(context) = interaction_context {
                if let Err(error) = context
                    .service
                    .invalidate_run(bcn_run_id, "run_deadline", bcs_protocol::now_ms())
                    .await
                {
                    warn!(run_id = %bcn_run_id, %error, "failed to invalidate expired interactions");
                }
            }
            return None;
        }
        let StreamEvent::Interaction(interaction) = event else {
            return None;
        };
        let Some(context) = interaction_context else {
            warn!(run_id = %bcn_run_id, "interaction service not wired; dropping frame");
            return None;
        };
        let Some(bcs_session_id) = bcs_session_id.clone() else {
            warn!(run_id = %bcn_run_id, "interaction has no trusted BCS session; dropping frame");
            return None;
        };
        let app_kind = match interaction.kind {
            WireInteractionKind::Exec => InteractionKind::Exec,
            WireInteractionKind::AskUser => InteractionKind::AskUser,
            WireInteractionKind::ModeSwitch => InteractionKind::ModeSwitch,
        };
        let result = match interaction.phase {
            InteractionPhase::Requested => context
                .service
                .on_provider_requested(ProviderInteractionRequestedCommand {
                    bcs_run_id: bcn_run_id.to_string(),
                    provider_run_id: interaction.run_id,
                    interaction_id: interaction.interaction_id,
                    kind: app_kind,
                    bcs_session_id,
                    group_id: group_id.to_string(),
                    bot_id: bot_id.to_string(),
                    run_deadline_ms: deadline_ms,
                    provider_target: context.provider_target.clone(),
                    provider_bypass_headers: context.provider_bypass_headers.clone(),
                    payload: interaction.raw,
                    received_at_ms: bcs_protocol::now_ms(),
                })
                .await
                .map(|_| ()),
            InteractionPhase::Resolved => {
                context
                    .service
                    .on_provider_resolved(ProviderInteractionResolvedCommand {
                        bcs_run_id: bcn_run_id.to_string(),
                        provider_run_id: interaction.run_id,
                        interaction_id: interaction.interaction_id,
                        kind: app_kind,
                        payload: interaction.raw,
                        received_at_ms: bcs_protocol::now_ms(),
                    })
                    .await
            }
        };
        if let Err(error) = result {
            warn!(run_id = %bcn_run_id, %error, "interaction frame handling failed");
        }
        return Some(false);
    }

    let (event_type, state, payload, terminal) = match kind {
        IngestKind::Drop => return None,
        IngestKind::Interaction => unreachable!("interaction handled above"),
        IngestKind::CloseUnsupported => {
            // Approval/HITL is gated this round (#4/D11): close the run with a
            // chat error terminal so the frontend isn't stuck. Not deduped.
            warn!(
                run_id = %bcn_run_id,
                "approval/HITL received but resolve is out of scope; closing run as unsupported"
            );
            let payload = build_chat_error_payload(bcn_run_id, group_id);
            (
                "chat.event".to_string(),
                ChatEventState::Error,
                payload,
                true,
            )
        }
        IngestKind::Pipeline { event_type, state } => {
            // #4: dedupe off the parsed StreamEvent's seq, never the SSE id.
            match dedup.accept(stream_event_seq(&event)) {
                SeqDecision::Duplicate => {
                    warn!(run_id = %bcn_run_id, "duplicate/regressed seq; dropping");
                    return None;
                }
                SeqDecision::Gap(gap) => warn!(run_id = %bcn_run_id, gap, "seq gap"),
                SeqDecision::Accept => {}
            }
            let payload = build_event_payload(&event, bcn_run_id, group_id, recv_ms);
            (event_type, state, payload, false)
        }
        IngestKind::Terminal { event_type, state } => {
            match dedup.accept(stream_event_seq(&event)) {
                SeqDecision::Duplicate => {
                    warn!(run_id = %bcn_run_id, "duplicate/regressed terminal seq; dropping");
                    return None;
                }
                SeqDecision::Gap(gap) => {
                    warn!(run_id = %bcn_run_id, gap, "seq gap before terminal")
                }
                SeqDecision::Accept => {}
            }
            let payload = build_event_payload(&event, bcn_run_id, group_id, recv_ms);
            (event_type, state, payload, true)
        }
    };

    // #2: re-check the run deadline per frame. The SSE connection is itself the
    // terminal writer, so a cheap monotonic deadline check is enough to avoid
    // feeding an already-expired run; drop + WARN (no raw payload) and move on.
    if bcs_protocol::now_ms() > deadline_ms {
        warn!(run_id = %bcn_run_id, "run deadline exceeded; dropping frame");
        return None;
    }

    ingest(
        bcn_run_id,
        group_id,
        bot_id,
        bcs_session_id,
        event_type,
        state,
        payload,
        flow,
    )
    .await;
    Some(terminal)
}
