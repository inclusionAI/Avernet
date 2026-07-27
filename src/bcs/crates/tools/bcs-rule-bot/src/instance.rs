use std::collections::{HashMap, HashSet, VecDeque};
use std::path::PathBuf;
use std::time::Duration;

use anyhow::{Context, Result, bail};
use bcs_protocol::{
    BCS_PROTOCOL_VERSION, BcsFrame, BotConnectParams, BotConnectResponse, BotStatus,
    BotStatusParams, ChatAbortParams, ChatInjectParams, ChatSendParams, ChatSendResponse,
    RequestFrame, ResponseFrame,
};
use futures::{SinkExt, StreamExt};
use serde_json::json;
use tokio::sync::mpsc;
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Message;
use tokio_util::sync::CancellationToken;
use tracing::{debug, info, warn};
use uuid::Uuid;

use crate::behavior::{
    BehaviorError, BehaviorInput, BehaviorOutcome, BehaviorRuntime, SupervisorStart,
};
use crate::config::{BehaviorConfig, BotProfile};
use crate::protocol::{
    ChatHistoryParams, HistoryMessage, SessionDeleteParams, TaskCompleteParams, TaskDispatchParams,
    TaskDispatchResponse, error_chat_event, error_response, final_chat_event, history_response,
    message_text, ok_response,
};
use crate::session_store::{SessionInfo, SessionStore};
use crate::status::{InstanceState, StatusCommand, StatusUpdate};

const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(30);
const CONNECT_TIMEOUT: Duration = Duration::from_secs(15);
const IDEMPOTENCY_CACHE_SIZE: usize = 1024;

#[derive(Debug, Clone)]
pub struct InstanceConfig {
    pub bot: BotProfile,
    pub scopes: String,
    pub bcs_url: String,
    pub profile_dir: PathBuf,
}

pub async fn run_instance(
    config: InstanceConfig,
    cancellation: CancellationToken,
    status_tx: mpsc::UnboundedSender<StatusCommand>,
) -> Result<()> {
    let behavior_config = config
        .bot
        .behavior()
        .context("rule bot is missing behavior")?
        .clone();
    let mut state = RuntimeState::new(&behavior_config);
    let mut reconnect_attempt = 0_u32;
    send_status(&config, &status_tx, InstanceState::Starting, None, None);

    loop {
        if cancellation.is_cancelled() {
            send_status(&config, &status_tx, InstanceState::Stopped, None, None);
            return Ok(());
        }

        match run_connection(&config, &mut state, &cancellation, &status_tx).await {
            Ok(()) if cancellation.is_cancelled() => {
                send_status(&config, &status_tx, InstanceState::Stopped, None, None);
                return Ok(());
            }
            Ok(()) => {
                reconnect_attempt = 0;
            }
            Err(error) => {
                warn!(
                    profile = %config.bot.profile,
                    error = %error,
                    "rule bot connection ended"
                );
                send_status(
                    &config,
                    &status_tx,
                    InstanceState::Reconnecting,
                    state.bot_uuid.clone(),
                    Some(error.to_string()),
                );
            }
        }

        let delay_secs = 1_u64
            .checked_shl(reconnect_attempt.min(5))
            .unwrap_or(32)
            .min(30);
        reconnect_attempt = reconnect_attempt.saturating_add(1);
        tokio::select! {
            () = cancellation.cancelled() => {
                send_status(&config, &status_tx, InstanceState::Stopped, state.bot_uuid.clone(), None);
                return Ok(());
            }
            () = tokio::time::sleep(Duration::from_secs(delay_secs)) => {}
        }
    }
}

async fn run_connection(
    config: &InstanceConfig,
    state: &mut RuntimeState,
    cancellation: &CancellationToken,
    status_tx: &mpsc::UnboundedSender<StatusCommand>,
) -> Result<()> {
    let session_store = SessionStore::new(&config.profile_dir);
    let saved_session = session_store.load()?;
    if let Some(session) = &saved_session
        && session.bcs_url != config.bcs_url
    {
        bail!(
            "saved BCS URL {} does not match configured URL {}; clean the bot session before reconnecting",
            session.bcs_url,
            config.bcs_url
        );
    }

    info!(
        profile = %config.bot.profile,
        bcs_url = %config.bcs_url,
        "connecting rule bot"
    );
    let (mut socket, _) = connect_async(&config.bcs_url)
        .await
        .with_context(|| format!("failed to connect {}", config.bcs_url))?;
    let connect_id = Uuid::new_v4().to_string();
    let connect = BcsFrame::Request(RequestFrame::new(
        &connect_id,
        "bot.connect",
        Some(serde_json::to_value(BotConnectParams {
            token: saved_session.as_ref().map(|session| session.token.clone()),
            bot_id: saved_session
                .as_ref()
                .and_then(|session| session.bot_uuid.clone()),
            protocol_version: Some(BCS_PROTOCOL_VERSION),
            client_kind: None,
        })?),
    ));
    socket
        .send(Message::Text(serde_json::to_string(&connect)?.into()))
        .await
        .context("failed to send bot.connect")?;

    let connect_response = tokio::time::timeout(CONNECT_TIMEOUT, async {
        loop {
            match socket.next().await {
                Some(Ok(Message::Text(text))) => {
                    let frame: BcsFrame = serde_json::from_str(&text)?;
                    if let BcsFrame::Response(response) = frame
                        && response.id == connect_id
                    {
                        if !response.ok {
                            let message = response
                                .error
                                .map_or_else(|| "unknown error".to_string(), |error| error.message);
                            bail!("bot.connect rejected: {message}");
                        }
                        let payload = response
                            .payload
                            .context("bot.connect response is missing payload")?;
                        let response: BotConnectResponse = serde_json::from_value(payload)?;
                        return Ok(response);
                    }
                }
                Some(Ok(Message::Ping(payload))) => {
                    socket.send(Message::Pong(payload)).await?;
                }
                Some(Ok(Message::Close(_))) | None => {
                    bail!("connection closed before bot.connect completed");
                }
                Some(Ok(_)) => {}
                Some(Err(error)) => return Err(anyhow::Error::from(error)),
            }
        }
    })
    .await
    .context("bot.connect timed out")??;

    let bot_uuid = connect_response.bot_uuid;
    session_store.save(&SessionInfo {
        bot_uuid: Some(bot_uuid.clone()),
        token: connect_response.token,
        bcs_url: config.bcs_url.clone(),
    })?;
    state.bot_uuid = Some(bot_uuid.clone());
    send_status(
        config,
        status_tx,
        InstanceState::Connected,
        Some(bot_uuid.clone()),
        None,
    );
    info!(
        profile = %config.bot.profile,
        bot_uuid = %bot_uuid,
        is_new = connect_response.is_new,
        "rule bot connected"
    );

    let (mut writer, mut reader) = socket.split();
    let (outgoing_tx, mut outgoing_rx) = mpsc::unbounded_channel::<BcsFrame>();
    let (internal_tx, mut internal_rx) = mpsc::unbounded_channel::<InternalEvent>();
    let mut heartbeat = tokio::time::interval(HEARTBEAT_INTERVAL);
    heartbeat.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);

    loop {
        tokio::select! {
            () = cancellation.cancelled() => {
                let _ = writer.send(Message::Close(None)).await;
                return Ok(());
            }
            Some(frame) = outgoing_rx.recv() => {
                writer
                    .send(Message::Text(serde_json::to_string(&frame)?.into()))
                    .await
                    .context("failed to send BCS frame")?;
            }
            Some(event) = internal_rx.recv() => {
                match event {
                    InternalEvent::RunFinished(run_id) => {
                        state.pending_runs.remove(&run_id);
                    }
                    InternalEvent::SupervisorAnnouncementFinished {
                        session_key,
                        manager_run_id,
                    } => {
                        state.pending_runs.remove(&manager_run_id);
                        begin_supervisor_dispatch(
                            state,
                            &session_key,
                            &manager_run_id,
                            &outgoing_tx,
                            &internal_tx,
                        )?;
                    }
                    InternalEvent::SupervisorTimeout {
                        session_key,
                        generation,
                    } => {
                        handle_supervisor_timeout(
                            state,
                            &session_key,
                            generation,
                            &outgoing_tx,
                            &internal_tx,
                        )?;
                    }
                }
            }
            _ = heartbeat.tick() => {
                let heartbeat_id = Uuid::new_v4().to_string();
                let busy = !state.pending_runs.is_empty() || !state.supervisions.is_empty();
                let frame = BcsFrame::Request(RequestFrame::new(
                    heartbeat_id,
                    "bot.status",
                    Some(serde_json::to_value(BotStatusParams {
                        status: Some(if busy {
                            BotStatus::Busy
                        } else {
                            BotStatus::Idle
                        }),
                        dynamic_summary: Some(format!("rule:{}", config.bot.behavior_name())),
                        load: Some(if busy { 1.0 } else { 0.0 }),
                    })?),
                ));
                writer
                    .send(Message::Text(serde_json::to_string(&frame)?.into()))
                    .await
                    .context("failed to send heartbeat")?;
                let _ = status_tx.send(StatusCommand::Touch(config.bot.profile.clone()));
            }
            incoming = reader.next() => {
                match incoming {
                    Some(Ok(Message::Text(text))) => {
                        let frame: BcsFrame = match serde_json::from_str(&text) {
                            Ok(frame) => frame,
                            Err(error) => {
                                warn!(
                                    profile = %config.bot.profile,
                                    error = %error,
                                    "ignoring invalid BCS frame"
                                );
                                continue;
                            }
                        };
                        handle_incoming(
                            config,
                            state,
                            frame,
                            &outgoing_tx,
                            &internal_tx,
                        )?;
                    }
                    Some(Ok(Message::Ping(payload))) => {
                        writer.send(Message::Pong(payload)).await?;
                    }
                    Some(Ok(Message::Close(_))) | None => {
                        bail!("BCS WebSocket closed");
                    }
                    Some(Ok(_)) => {}
                    Some(Err(error)) => return Err(error.into()),
                }
            }
        }
    }
}

fn handle_incoming(
    config: &InstanceConfig,
    state: &mut RuntimeState,
    frame: BcsFrame,
    outgoing_tx: &mpsc::UnboundedSender<BcsFrame>,
    internal_tx: &mpsc::UnboundedSender<InternalEvent>,
) -> Result<()> {
    match frame {
        BcsFrame::Request(request) => match request.method.as_str() {
            "chat.send" => handle_chat_send(config, state, request, outgoing_tx, internal_tx),
            "chat.inject" => handle_chat_inject(state, request, outgoing_tx),
            "chat.history" => handle_chat_history(state, request, outgoing_tx),
            "chat.abort" => handle_chat_abort(state, request, outgoing_tx),
            "session.delete" => handle_session_delete(state, request, outgoing_tx),
            method => {
                send(
                    outgoing_tx,
                    error_response(
                        request.id,
                        "unknown_method",
                        format!("unknown method: {method}"),
                        false,
                    ),
                );
                Ok(())
            }
        },
        BcsFrame::Response(response) => {
            handle_bcs_response(state, response, outgoing_tx, internal_tx)
        }
        BcsFrame::Event(event) => {
            debug!(event = %event.event, "received BCS event");
            Ok(())
        }
    }
}

fn handle_chat_send(
    config: &InstanceConfig,
    state: &mut RuntimeState,
    request: RequestFrame,
    outgoing_tx: &mpsc::UnboundedSender<BcsFrame>,
    internal_tx: &mpsc::UnboundedSender<InternalEvent>,
) -> Result<()> {
    let task_id = request
        .params
        .as_ref()
        .and_then(|params| params.get("task_id"))
        .and_then(|value| value.as_str())
        .map(str::to_string);
    let params: ChatSendParams = match parse_params(&request) {
        Ok(params) => params,
        Err(error) => {
            send(
                outgoing_tx,
                error_response(request.id, "invalid_params", error.to_string(), false),
            );
            return Ok(());
        }
    };
    let input = behavior_input(&params, task_id);
    let cache_key = state.cache_key(&input, params.idempotency_key.as_deref(), &request.id);

    if let Some(cached) = state.idempotency.get(&cache_key).cloned() {
        send(
            outgoing_tx,
            ok_response(
                &request.id,
                serde_json::to_value(ChatSendResponse {
                    run_id: cached.run_id.clone(),
                })?,
            ),
        );
        if let Some(reply) = cached.reply
            && !state.pending_runs.contains_key(&cached.run_id)
        {
            schedule_reply(
                state,
                cached.run_id,
                cached.group_id,
                cached.session_key,
                reply,
                0,
                outgoing_tx.clone(),
                internal_tx.clone(),
            );
        }
        return Ok(());
    }

    let run_id = request.id.clone();
    send(
        outgoing_tx,
        ok_response(
            &request.id,
            serde_json::to_value(ChatSendResponse {
                run_id: run_id.clone(),
            })?,
        ),
    );
    state.append_history(
        input.effective_session_key(),
        HistoryMessage {
            id: request.id.clone(),
            role: params.message.role.clone(),
            content: input.message_text.clone(),
            timestamp: params.message.timestamp,
        },
    );

    if input.task_id.is_some() && handle_supervisor_result(state, &input, outgoing_tx, internal_tx)?
    {
        state.idempotency.insert(
            cache_key,
            CachedRun {
                run_id,
                group_id: params.bcs_group_id,
                session_key: input.effective_session_key().to_string(),
                reply: None,
            },
        );
        return Ok(());
    }

    if state
        .supervisions
        .contains_key(input.effective_session_key())
    {
        complete_reply(
            state,
            cache_key,
            run_id,
            params.bcs_group_id,
            input.effective_session_key(),
            "当前会话已有协调任务正在执行，请等待任务完成。".to_string(),
            config.bot.response_delay_ms(),
            outgoing_tx,
            internal_tx,
        );
        return Ok(());
    }

    match state.behavior.handle_send(&input) {
        Ok(BehaviorOutcome::Reply(reply)) => {
            state.idempotency.insert(
                cache_key,
                CachedRun {
                    run_id: run_id.clone(),
                    group_id: params.bcs_group_id.clone(),
                    session_key: input.effective_session_key().to_string(),
                    reply: Some(reply.clone()),
                },
            );
            state.append_history(
                input.effective_session_key(),
                HistoryMessage {
                    id: format!("rule-{run_id}"),
                    role: "assistant".to_string(),
                    content: reply.clone(),
                    timestamp: bcs_protocol::now_ms(),
                },
            );
            schedule_reply(
                state,
                run_id,
                params.bcs_group_id,
                input.effective_session_key().to_string(),
                reply,
                config.bot.response_delay_ms(),
                outgoing_tx.clone(),
                internal_tx.clone(),
            );
        }
        Ok(BehaviorOutcome::StartSupervisor(start)) => {
            start_supervisor(
                config,
                state,
                &input,
                run_id,
                params.bcs_group_id,
                cache_key,
                start,
                outgoing_tx,
                internal_tx,
            )?;
        }
        Err(error) => {
            let (kind, message) = behavior_error(&error);
            send(
                outgoing_tx,
                error_chat_event(run_id, params.bcs_group_id, kind, message),
            );
        }
    }
    Ok(())
}

fn handle_chat_inject(
    state: &mut RuntimeState,
    request: RequestFrame,
    outgoing_tx: &mpsc::UnboundedSender<BcsFrame>,
) -> Result<()> {
    let params: ChatInjectParams = match parse_params(&request) {
        Ok(params) => params,
        Err(error) => {
            send(
                outgoing_tx,
                error_response(request.id, "invalid_params", error.to_string(), false),
            );
            return Ok(());
        }
    };
    let effective_session = params
        .bcs_session_id
        .as_deref()
        .filter(|value| !value.is_empty())
        .unwrap_or(params.session_key.as_str())
        .to_string();
    let content = message_text(&params.message);
    state.append_history(
        &effective_session,
        HistoryMessage {
            id: request.id.clone(),
            role: params.message.role,
            content,
            timestamp: params.message.timestamp,
        },
    );
    send(
        outgoing_tx,
        ok_response(request.id, json!({"injected": true})),
    );
    Ok(())
}

fn handle_chat_history(
    state: &mut RuntimeState,
    request: RequestFrame,
    outgoing_tx: &mpsc::UnboundedSender<BcsFrame>,
) -> Result<()> {
    let params: ChatHistoryParams = match parse_params(&request) {
        Ok(params) => params,
        Err(error) => {
            send(
                outgoing_tx,
                error_response(request.id, "invalid_params", error.to_string(), false),
            );
            return Ok(());
        }
    };
    let messages = state
        .history
        .get(&params.session_key)
        .map_or(&[][..], Vec::as_slice);
    send(
        outgoing_tx,
        history_response(&request.id, &params.session_key, messages, &params),
    );
    Ok(())
}

fn handle_chat_abort(
    state: &mut RuntimeState,
    request: RequestFrame,
    outgoing_tx: &mpsc::UnboundedSender<BcsFrame>,
) -> Result<()> {
    let params: ChatAbortParams = match parse_params(&request) {
        Ok(params) => params,
        Err(error) => {
            send(
                outgoing_tx,
                error_response(request.id, "invalid_params", error.to_string(), false),
            );
            return Ok(());
        }
    };
    let mut aborted = 0_usize;
    if let Some(run_id) = params.run_id {
        if let Some(pending) = state.pending_runs.remove(&run_id) {
            pending.cancellation.cancel();
            aborted = 1;
        }
        let supervision_session = state
            .supervisions
            .iter()
            .find_map(|(session, run)| (run.manager_run_id == run_id).then(|| session.clone()));
        if let Some(session) = supervision_session {
            state.supervisions.remove(&session);
            state
                .dispatch_requests
                .retain(|_, pending| pending.session_key != session);
            aborted = aborted.saturating_add(1);
        }
    } else {
        for (_, pending) in state.pending_runs.drain() {
            pending.cancellation.cancel();
            aborted = aborted.saturating_add(1);
        }
        aborted = aborted.saturating_add(state.supervisions.len());
        state.supervisions.clear();
        state.dispatch_requests.clear();
    }
    send(
        outgoing_tx,
        ok_response(request.id, json!({"aborted": aborted})),
    );
    Ok(())
}

fn handle_session_delete(
    state: &mut RuntimeState,
    request: RequestFrame,
    outgoing_tx: &mpsc::UnboundedSender<BcsFrame>,
) -> Result<()> {
    let params: SessionDeleteParams = match parse_params(&request) {
        Ok(params) => params,
        Err(error) => {
            send(
                outgoing_tx,
                error_response(request.id, "invalid_params", error.to_string(), false),
            );
            return Ok(());
        }
    };
    let matching = state
        .history
        .keys()
        .filter(|key| session_belongs_to_group(key, &params.bcs_group_id))
        .cloned()
        .collect::<Vec<_>>();
    let pending_run_ids = state
        .pending_runs
        .iter()
        .filter(|(_, pending)| session_belongs_to_group(&pending.session_key, &params.bcs_group_id))
        .map(|(run_id, _)| run_id.clone())
        .collect::<Vec<_>>();
    for run_id in pending_run_ids {
        if let Some(pending) = state.pending_runs.remove(&run_id) {
            pending.cancellation.cancel();
        }
    }
    for key in matching {
        state.history.remove(&key);
        state.behavior.clear_session(&key);
    }
    state.idempotency.remove_group(&params.bcs_group_id);
    state
        .supervisions
        .retain(|session, _| !session_belongs_to_group(session, &params.bcs_group_id));
    state
        .dispatch_requests
        .retain(|_, pending| !session_belongs_to_group(&pending.session_key, &params.bcs_group_id));
    send(
        outgoing_tx,
        ok_response(request.id, json!({"deleted": true})),
    );
    Ok(())
}

fn session_belongs_to_group(session_key: &str, group_id: &str) -> bool {
    session_key == group_id
        || session_key
            .strip_prefix(group_id)
            .is_some_and(|suffix| suffix.starts_with(':'))
}

fn behavior_input(params: &ChatSendParams, task_id: Option<String>) -> BehaviorInput {
    let wire_message = message_text(&params.message);
    let routed_message = if params.session_context.message.is_empty() {
        wire_message
    } else {
        params.session_context.message.clone()
    };
    let normalized_message = strip_leading_recipient_mention(
        &routed_message,
        params.session_context.you_are_mentioned,
        params.session_context.recipient_name.as_deref(),
        params.session_context.recipient.as_deref(),
        &params.session_context.mentions,
    );
    BehaviorInput {
        session_key: params.session_key.clone(),
        bcs_group_id: params.bcs_group_id.clone(),
        bcs_session_id: params.bcs_session_id.clone(),
        message_text: normalized_message.clone(),
        context_message: normalized_message,
        task_id,
        sender_name: params.session_context.from.clone(),
        recipient_role: params.session_context.recipient_role.clone(),
        group_type: params.session_context.group_type.clone(),
        participants: params.session_context.participants.clone(),
    }
}

fn strip_leading_recipient_mention(
    message: &str,
    you_are_mentioned: bool,
    recipient_name: Option<&str>,
    recipient_id: Option<&str>,
    mentions: &[String],
) -> String {
    if !you_are_mentioned {
        return message.to_string();
    }

    let trimmed = message.trim_start();
    let without_at = trimmed.strip_prefix('@').unwrap_or(trimmed);
    let candidates = recipient_name
        .into_iter()
        .chain(recipient_id)
        .chain(mentions.iter().map(String::as_str));
    for candidate in candidates {
        let candidate = candidate.strip_prefix('@').unwrap_or(candidate);
        if candidate.is_empty() {
            continue;
        }
        let Some(remainder) = without_at.strip_prefix(candidate) else {
            continue;
        };
        if remainder.is_empty() {
            return String::new();
        }
        if remainder.chars().next().is_some_and(is_mention_separator) {
            return remainder
                .trim_start_matches(is_mention_separator)
                .to_string();
        }
    }
    message.to_string()
}

fn is_mention_separator(ch: char) -> bool {
    ch.is_whitespace() || matches!(ch, ':' | '：' | ',' | '，')
}

#[allow(clippy::too_many_arguments)]
fn start_supervisor(
    config: &InstanceConfig,
    state: &mut RuntimeState,
    input: &BehaviorInput,
    run_id: String,
    reply_group_id: String,
    cache_key: String,
    start: SupervisorStart,
    outgoing_tx: &mpsc::UnboundedSender<BcsFrame>,
    internal_tx: &mpsc::UnboundedSender<InternalEvent>,
) -> Result<()> {
    let session_key = input.effective_session_key().to_string();
    let invalid_context = if input.group_type.as_deref() != Some("manager_worker") {
        Some("任务协调者只能在 manager_worker 群中启动。")
    } else if input.recipient_role.as_deref() != Some("manager") {
        Some("只有 manager 角色可以启动任务协调。")
    } else {
        None
    };
    if let Some(message) = invalid_context {
        complete_reply(
            state,
            cache_key,
            run_id,
            reply_group_id,
            &session_key,
            message.to_string(),
            config.bot.response_delay_ms(),
            outgoing_tx,
            internal_tx,
        );
        return Ok(());
    }
    let supervisor_input = if input.sender_name == "bcs-system-message" {
        match supervisor_system_task(&input.message_text) {
            Some(task) => task,
            None => {
                complete_reply(
                    state,
                    cache_key,
                    run_id,
                    reply_group_id,
                    &session_key,
                    "任务协调者已就绪，等待任务。".to_string(),
                    config.bot.response_delay_ms(),
                    outgoing_tx,
                    internal_tx,
                );
                return Ok(());
            }
        }
    } else {
        input.message_text.clone()
    };

    let own_bot_uuid = state.bot_uuid.as_deref();
    let members = input
        .participants
        .iter()
        .map(|member| parse_participant_identity(member))
        .filter(|member| {
            member.name != config.bot.name && Some(member.target.as_str()) != own_bot_uuid
        })
        .collect::<Vec<_>>();
    if members.is_empty() {
        complete_reply(
            state,
            cache_key,
            run_id,
            reply_group_id,
            &session_key,
            "任务协调者没有找到可分配任务的成员。".to_string(),
            config.bot.response_delay_ms(),
            outgoing_tx,
            internal_tx,
        );
        return Ok(());
    }
    let mut unique = HashSet::new();
    if let Some(duplicate) = members
        .iter()
        .find(|member| !unique.insert(member.name.as_str()))
    {
        complete_reply(
            state,
            cache_key,
            run_id,
            reply_group_id,
            &session_key,
            format!(
                "任务协调者无法派发任务：成员名称重复（{}）。",
                duplicate.name
            ),
            config.bot.response_delay_ms(),
            outgoing_tx,
            internal_tx,
        );
        return Ok(());
    }

    let total = members.len();
    let announcement = format!("已收到任务，开始分配给 {total} 位成员。");
    let member_runs = members
        .into_iter()
        .enumerate()
        .map(|(index, member)| SupervisionMember {
            task_message: render_assignment(
                &start.assignment.task_template,
                &supervisor_input,
                &member.name,
                index + 1,
                total,
            ),
            name: member.name,
            target_bot: member.target,
            attempts: 0,
            status: SupervisionMemberStatus::Dispatching,
        })
        .collect::<Vec<_>>();
    let scope_id = input
        .bcs_session_id
        .as_deref()
        .filter(|value| !value.is_empty())
        .unwrap_or(input.bcs_group_id.as_str())
        .to_string();

    state.idempotency.insert(
        cache_key.clone(),
        CachedRun {
            run_id: run_id.clone(),
            group_id: reply_group_id.clone(),
            session_key: session_key.clone(),
            reply: Some(announcement.clone()),
        },
    );
    state.append_history(
        &session_key,
        HistoryMessage {
            id: format!("rule-{run_id}"),
            role: "assistant".to_string(),
            content: announcement.clone(),
            timestamp: bcs_protocol::now_ms(),
        },
    );
    state.supervisions.insert(
        session_key.clone(),
        SupervisionRun {
            manager_run_id: run_id.clone(),
            scope_id,
            original_input: supervisor_input,
            summary_template: start.summary_template,
            response_delay_ms: config.bot.response_delay_ms(),
            timeout_ms: start.completion.timeout_ms,
            max_retries: start.completion.max_retries,
            generation: 1,
            dispatch_started: false,
            members: member_runs,
        },
    );

    let response_delay_ms = config.bot.response_delay_ms();
    if response_delay_ms == 0 {
        send(
            outgoing_tx,
            final_chat_event(&run_id, reply_group_id, announcement),
        );
        begin_supervisor_dispatch(state, &session_key, &run_id, outgoing_tx, internal_tx)?;
    } else {
        schedule_supervisor_announcement(
            state,
            run_id,
            reply_group_id,
            session_key,
            announcement,
            response_delay_ms,
            outgoing_tx.clone(),
            internal_tx.clone(),
        );
    }
    Ok(())
}

fn begin_supervisor_dispatch(
    state: &mut RuntimeState,
    session_key: &str,
    manager_run_id: &str,
    outgoing_tx: &mpsc::UnboundedSender<BcsFrame>,
    internal_tx: &mpsc::UnboundedSender<InternalEvent>,
) -> Result<()> {
    let Some(run) = state.supervisions.get_mut(session_key) else {
        return Ok(());
    };
    if run.manager_run_id != manager_run_id || run.dispatch_started {
        return Ok(());
    }
    run.dispatch_started = true;
    let member_count = run.members.len();
    let generation = run.generation;
    let timeout_ms = run.timeout_ms;

    for member_index in 0..member_count {
        dispatch_supervision_member(state, session_key, member_index, outgoing_tx)?;
    }
    schedule_supervisor_timeout(
        session_key.to_string(),
        generation,
        timeout_ms,
        internal_tx.clone(),
    );
    Ok(())
}

fn supervisor_system_task(message: &str) -> Option<String> {
    tagged_task(message).or_else(|| manager_worker_group_goal(message))
}

fn tagged_task(message: &str) -> Option<String> {
    let (_, after_open) = message.split_once("[任务]")?;
    let (task, _) = after_open.split_once("[/任务]")?;
    let task = task.trim();
    (!task.is_empty()).then(|| task.to_string())
}

fn manager_worker_group_goal(message: &str) -> Option<String> {
    let (_, service_context) = message.split_once("[SERVICE GROUP CONTEXT]")?;
    let (service_context, _) = service_context.split_once("[/SERVICE GROUP CONTEXT]")?;
    let (_, participants_and_goal) = service_context.split_once("参与者:\n")?;
    let (roster_and_goal, _) = participants_and_goal.rsplit_once("\n[协同提醒]")?;
    let (_, goal) = roster_and_goal.split_once("\n\n")?;
    let goal = goal.trim();
    (!goal.is_empty()).then(|| goal.to_string())
}

fn parse_participant_identity(value: &str) -> ParticipantIdentity {
    let Some(without_closing) = value.strip_suffix(')') else {
        return ParticipantIdentity {
            name: value.to_string(),
            target: value.to_string(),
        };
    };
    let Some((name, identifier)) = without_closing.rsplit_once('(') else {
        return ParticipantIdentity {
            name: value.to_string(),
            target: value.to_string(),
        };
    };
    if name.is_empty() || identifier.is_empty() {
        return ParticipantIdentity {
            name: value.to_string(),
            target: value.to_string(),
        };
    }
    ParticipantIdentity {
        name: name.to_string(),
        target: identifier.to_string(),
    }
}

fn render_assignment(
    template: &str,
    input: &str,
    member: &str,
    index: usize,
    total: usize,
) -> String {
    template
        .replace("{input}", input)
        .replace("{member}", member)
        .replace("{index}", &index.to_string())
        .replace("{total}", &total.to_string())
}

fn dispatch_supervision_member(
    state: &mut RuntimeState,
    session_key: &str,
    member_index: usize,
    outgoing_tx: &mpsc::UnboundedSender<BcsFrame>,
) -> Result<()> {
    let request_id = Uuid::new_v4().to_string();
    let (scope_id, target_bot, message) = {
        let run = state
            .supervisions
            .get_mut(session_key)
            .context("supervision run disappeared before dispatch")?;
        let member = run
            .members
            .get_mut(member_index)
            .context("supervision member disappeared before dispatch")?;
        member.attempts = member.attempts.saturating_add(1);
        member.status = SupervisionMemberStatus::Dispatching;
        (
            run.scope_id.clone(),
            member.target_bot.clone(),
            member.task_message.clone(),
        )
    };
    state.dispatch_requests.insert(
        request_id.clone(),
        PendingDispatch {
            session_key: session_key.to_string(),
            member_index,
        },
    );
    send(
        outgoing_tx,
        BcsFrame::Request(RequestFrame::new(
            request_id,
            "task.dispatch",
            Some(serde_json::to_value(TaskDispatchParams {
                group_id: &scope_id,
                target_bot: &target_bot,
                message: &message,
            })?),
        )),
    );
    Ok(())
}

fn handle_bcs_response(
    state: &mut RuntimeState,
    response: ResponseFrame,
    outgoing_tx: &mpsc::UnboundedSender<BcsFrame>,
    internal_tx: &mpsc::UnboundedSender<InternalEvent>,
) -> Result<()> {
    let Some(pending) = state.dispatch_requests.remove(&response.id) else {
        debug!(id = %response.id, ok = response.ok, "received BCS response");
        return Ok(());
    };
    let Some(run) = state.supervisions.get_mut(&pending.session_key) else {
        return Ok(());
    };
    let Some(member) = run.members.get_mut(pending.member_index) else {
        return Ok(());
    };
    if !matches!(member.status, SupervisionMemberStatus::Dispatching) {
        return Ok(());
    }

    let dispatch_result = if response.ok {
        response
            .payload
            .ok_or_else(|| "task.dispatch response is missing payload".to_string())
            .and_then(|payload| {
                serde_json::from_value::<TaskDispatchResponse>(payload)
                    .map_err(|error| format!("invalid task.dispatch response: {error}"))
            })
            .map(|result| result.task_id)
    } else {
        Err(response
            .error
            .map_or_else(|| "task.dispatch failed".to_string(), |error| error.message))
    };

    match dispatch_result {
        Ok(task_id) => {
            member.status = SupervisionMemberStatus::Waiting { task_id };
        }
        Err(message) => {
            if member.attempts <= run.max_retries {
                dispatch_supervision_member(
                    state,
                    &pending.session_key,
                    pending.member_index,
                    outgoing_tx,
                )?;
            } else {
                member.status = SupervisionMemberStatus::DispatchFailed { message };
            }
        }
    }
    maybe_finalize_supervision(state, &pending.session_key, outgoing_tx, internal_tx)?;
    Ok(())
}

fn handle_supervisor_result(
    state: &mut RuntimeState,
    input: &BehaviorInput,
    outgoing_tx: &mpsc::UnboundedSender<BcsFrame>,
    internal_tx: &mpsc::UnboundedSender<InternalEvent>,
) -> Result<bool> {
    let Some(task_id) = input.task_id.as_deref() else {
        return Ok(false);
    };
    let matching_session = state.supervisions.iter().find_map(|(session_key, run)| {
        run.members
            .iter()
            .any(|member| {
                member.name == input.sender_name
                    && match &member.status {
                        SupervisionMemberStatus::Waiting { task_id: expected }
                        | SupervisionMemberStatus::Completed {
                            task_id: expected, ..
                        } => expected == task_id,
                        _ => false,
                    }
            })
            .then(|| session_key.clone())
    });
    let Some(session_key) = matching_session else {
        return Ok(false);
    };
    let run = state
        .supervisions
        .get_mut(&session_key)
        .context("matching supervision run disappeared")?;
    let Some(member) = run.members.iter_mut().find(|member| {
        member.name == input.sender_name
            && match &member.status {
                SupervisionMemberStatus::Waiting { task_id: expected }
                | SupervisionMemberStatus::Completed {
                    task_id: expected, ..
                } => expected == task_id,
                _ => false,
            }
    }) else {
        return Ok(false);
    };

    if matches!(member.status, SupervisionMemberStatus::Completed { .. }) {
        return Ok(true);
    }
    let result = if input.context_message.is_empty() {
        input.message_text.clone()
    } else {
        input.context_message.clone()
    };
    member.status = SupervisionMemberStatus::Completed {
        task_id: task_id.to_string(),
        result,
    };
    maybe_finalize_supervision(state, &session_key, outgoing_tx, internal_tx)?;
    Ok(true)
}

fn handle_supervisor_timeout(
    state: &mut RuntimeState,
    session_key: &str,
    generation: u64,
    outgoing_tx: &mpsc::UnboundedSender<BcsFrame>,
    internal_tx: &mpsc::UnboundedSender<InternalEvent>,
) -> Result<()> {
    let Some(run) = state.supervisions.get(session_key) else {
        return Ok(());
    };
    if run.generation != generation {
        return Ok(());
    }

    let retry_indexes = run
        .members
        .iter()
        .enumerate()
        .filter_map(|(index, member)| {
            (!member.status.is_terminal() && member.attempts <= run.max_retries).then_some(index)
        })
        .collect::<Vec<_>>();
    if retry_indexes.is_empty() {
        if let Some(run) = state.supervisions.get_mut(session_key) {
            for member in &mut run.members {
                if !member.status.is_terminal() {
                    member.status = SupervisionMemberStatus::Timeout;
                }
            }
        }
        state
            .dispatch_requests
            .retain(|_, pending| pending.session_key != session_key);
        maybe_finalize_supervision(state, session_key, outgoing_tx, internal_tx)?;
        return Ok(());
    }

    state
        .dispatch_requests
        .retain(|_, pending| pending.session_key != session_key);
    let (next_generation, timeout_ms) = {
        let run = state
            .supervisions
            .get_mut(session_key)
            .context("supervision run disappeared before retry")?;
        run.generation = run.generation.saturating_add(1);
        (run.generation, run.timeout_ms)
    };
    for member_index in retry_indexes {
        dispatch_supervision_member(state, session_key, member_index, outgoing_tx)?;
    }
    schedule_supervisor_timeout(
        session_key.to_string(),
        next_generation,
        timeout_ms,
        internal_tx.clone(),
    );
    Ok(())
}

fn schedule_supervisor_timeout(
    session_key: String,
    generation: u64,
    timeout_ms: u64,
    internal_tx: mpsc::UnboundedSender<InternalEvent>,
) {
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(timeout_ms)).await;
        let _ = internal_tx.send(InternalEvent::SupervisorTimeout {
            session_key,
            generation,
        });
    });
}

fn maybe_finalize_supervision(
    state: &mut RuntimeState,
    session_key: &str,
    outgoing_tx: &mpsc::UnboundedSender<BcsFrame>,
    internal_tx: &mpsc::UnboundedSender<InternalEvent>,
) -> Result<()> {
    let complete = state
        .supervisions
        .get(session_key)
        .is_some_and(|run| run.members.iter().all(|member| member.status.is_terminal()));
    if !complete {
        return Ok(());
    }
    let run = state
        .supervisions
        .remove(session_key)
        .context("completed supervision run disappeared")?;
    state
        .dispatch_requests
        .retain(|_, pending| pending.session_key != session_key);

    let success_count = run
        .members
        .iter()
        .filter(|member| matches!(member.status, SupervisionMemberStatus::Completed { .. }))
        .count();
    let failed_count = run.members.len().saturating_sub(success_count);
    let results = run
        .members
        .iter()
        .map(SupervisionMember::result_line)
        .collect::<Vec<_>>()
        .join("\n");
    let summary = run
        .summary_template
        .replace("{success_count}", &success_count.to_string())
        .replace("{failed_count}", &failed_count.to_string())
        .replace("{results}", &results)
        .replace("{input}", &run.original_input);

    send(
        outgoing_tx,
        BcsFrame::Request(RequestFrame::new(
            Uuid::new_v4().to_string(),
            "task.complete",
            Some(serde_json::to_value(TaskCompleteParams {
                group_id: &run.scope_id,
                summary: &summary,
                status: "completed",
            })?),
        )),
    );
    let summary_run_id = Uuid::new_v4().to_string();
    let summary_group_id = run.scope_id;
    let response_delay_ms = run.response_delay_ms;
    state.append_history(
        session_key,
        HistoryMessage {
            id: format!("rule-{summary_run_id}"),
            role: "assistant".to_string(),
            content: summary.clone(),
            timestamp: bcs_protocol::now_ms(),
        },
    );
    schedule_reply(
        state,
        summary_run_id,
        summary_group_id,
        session_key.to_string(),
        summary,
        response_delay_ms,
        outgoing_tx.clone(),
        internal_tx.clone(),
    );
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn complete_reply(
    state: &mut RuntimeState,
    cache_key: String,
    run_id: String,
    group_id: String,
    session_key: &str,
    reply: String,
    response_delay_ms: u64,
    outgoing_tx: &mpsc::UnboundedSender<BcsFrame>,
    internal_tx: &mpsc::UnboundedSender<InternalEvent>,
) {
    state.append_history(
        session_key,
        HistoryMessage {
            id: format!("rule-{run_id}"),
            role: "assistant".to_string(),
            content: reply.clone(),
            timestamp: bcs_protocol::now_ms(),
        },
    );
    state.idempotency.insert(
        cache_key,
        CachedRun {
            run_id: run_id.clone(),
            group_id: group_id.clone(),
            session_key: session_key.to_string(),
            reply: Some(reply.clone()),
        },
    );
    schedule_reply(
        state,
        run_id,
        group_id,
        session_key.to_string(),
        reply,
        response_delay_ms,
        outgoing_tx.clone(),
        internal_tx.clone(),
    );
}

fn parse_params<T: serde::de::DeserializeOwned>(request: &RequestFrame) -> Result<T> {
    let params = request
        .params
        .clone()
        .context("request is missing params")?;
    serde_json::from_value(params).context("invalid request params")
}

#[allow(clippy::too_many_arguments)]
fn schedule_reply(
    state: &mut RuntimeState,
    run_id: String,
    group_id: String,
    session_key: String,
    reply: String,
    delay_ms: u64,
    outgoing_tx: mpsc::UnboundedSender<BcsFrame>,
    internal_tx: mpsc::UnboundedSender<InternalEvent>,
) {
    if delay_ms == 0 {
        send(&outgoing_tx, final_chat_event(run_id, group_id, reply));
        return;
    }
    if let Some(previous) = state.pending_runs.remove(&run_id) {
        previous.cancellation.cancel();
    }
    let cancellation = CancellationToken::new();
    state.pending_runs.insert(
        run_id.clone(),
        PendingRun {
            session_key,
            cancellation: cancellation.clone(),
        },
    );
    tokio::spawn(async move {
        tokio::select! {
            () = cancellation.cancelled() => {}
            () = tokio::time::sleep(Duration::from_millis(delay_ms)) => {
                let _ = outgoing_tx.send(final_chat_event(&run_id, group_id, reply));
            }
        }
        let _ = internal_tx.send(InternalEvent::RunFinished(run_id));
    });
}

#[allow(clippy::too_many_arguments)]
fn schedule_supervisor_announcement(
    state: &mut RuntimeState,
    run_id: String,
    group_id: String,
    session_key: String,
    announcement: String,
    delay_ms: u64,
    outgoing_tx: mpsc::UnboundedSender<BcsFrame>,
    internal_tx: mpsc::UnboundedSender<InternalEvent>,
) {
    if let Some(previous) = state.pending_runs.remove(&run_id) {
        previous.cancellation.cancel();
    }
    let cancellation = CancellationToken::new();
    state.pending_runs.insert(
        run_id.clone(),
        PendingRun {
            session_key: session_key.clone(),
            cancellation: cancellation.clone(),
        },
    );
    tokio::spawn(async move {
        tokio::select! {
            () = cancellation.cancelled() => {
                let _ = internal_tx.send(InternalEvent::RunFinished(run_id));
            }
            () = tokio::time::sleep(Duration::from_millis(delay_ms)) => {
                let _ = outgoing_tx.send(final_chat_event(&run_id, group_id, announcement));
                let _ = internal_tx.send(InternalEvent::SupervisorAnnouncementFinished {
                    session_key,
                    manager_run_id: run_id,
                });
            }
        }
    });
}

fn behavior_error(error: &BehaviorError) -> (&'static str, String) {
    match error {
        BehaviorError::OutputSizeOverflow => ("output_size_overflow", error.to_string()),
        BehaviorError::ResourceExhausted => ("resource_exhausted", error.to_string()),
    }
}

fn send(outgoing_tx: &mpsc::UnboundedSender<BcsFrame>, frame: BcsFrame) {
    let _ = outgoing_tx.send(frame);
}

fn send_status(
    config: &InstanceConfig,
    status_tx: &mpsc::UnboundedSender<StatusCommand>,
    state: InstanceState,
    bot_uuid: Option<String>,
    last_error: Option<String>,
) {
    let _ = status_tx.send(StatusCommand::Update(StatusUpdate {
        profile: config.bot.profile.clone(),
        name: config.bot.name.clone(),
        behavior: config.bot.behavior_name().to_string(),
        state,
        bot_uuid,
        last_error,
    }));
}

struct RuntimeState {
    bot_uuid: Option<String>,
    behavior: BehaviorRuntime,
    history: HashMap<String, Vec<HistoryMessage>>,
    idempotency: IdempotencyCache,
    pending_runs: HashMap<String, PendingRun>,
    supervisions: HashMap<String, SupervisionRun>,
    dispatch_requests: HashMap<String, PendingDispatch>,
}

impl RuntimeState {
    fn new(config: &BehaviorConfig) -> Self {
        Self {
            bot_uuid: None,
            behavior: BehaviorRuntime::new(config),
            history: HashMap::new(),
            idempotency: IdempotencyCache::new(IDEMPOTENCY_CACHE_SIZE),
            pending_runs: HashMap::new(),
            supervisions: HashMap::new(),
            dispatch_requests: HashMap::new(),
        }
    }

    fn append_history(&mut self, session_key: &str, message: HistoryMessage) {
        self.history
            .entry(session_key.to_string())
            .or_default()
            .push(message);
    }

    fn cache_key(
        &self,
        input: &BehaviorInput,
        idempotency_key: Option<&str>,
        request_id: &str,
    ) -> String {
        format!(
            "{}\0{}",
            input.effective_session_key(),
            idempotency_key.unwrap_or(request_id)
        )
    }
}

#[derive(Debug, Clone)]
struct CachedRun {
    run_id: String,
    group_id: String,
    session_key: String,
    reply: Option<String>,
}

struct PendingRun {
    session_key: String,
    cancellation: CancellationToken,
}

struct SupervisionRun {
    manager_run_id: String,
    scope_id: String,
    original_input: String,
    summary_template: String,
    response_delay_ms: u64,
    timeout_ms: u64,
    max_retries: u32,
    generation: u64,
    dispatch_started: bool,
    members: Vec<SupervisionMember>,
}

struct SupervisionMember {
    name: String,
    target_bot: String,
    task_message: String,
    attempts: u32,
    status: SupervisionMemberStatus,
}

struct ParticipantIdentity {
    name: String,
    target: String,
}

enum SupervisionMemberStatus {
    Dispatching,
    Waiting { task_id: String },
    Completed { task_id: String, result: String },
    DispatchFailed { message: String },
    Timeout,
}

impl SupervisionMemberStatus {
    fn is_terminal(&self) -> bool {
        matches!(
            self,
            Self::Completed { .. } | Self::DispatchFailed { .. } | Self::Timeout
        )
    }
}

impl SupervisionMember {
    fn result_line(&self) -> String {
        match &self.status {
            SupervisionMemberStatus::Completed { result, .. } => {
                format!("- {} [completed]: {}", self.name, result)
            }
            SupervisionMemberStatus::DispatchFailed { message } => {
                format!("- {} [dispatch_failed]: {}", self.name, message)
            }
            SupervisionMemberStatus::Timeout => {
                format!("- {} [timeout]: 未在期限内返回", self.name)
            }
            SupervisionMemberStatus::Dispatching | SupervisionMemberStatus::Waiting { .. } => {
                format!("- {} [incomplete]: 状态未结束", self.name)
            }
        }
    }
}

struct PendingDispatch {
    session_key: String,
    member_index: usize,
}

struct IdempotencyCache {
    capacity: usize,
    values: HashMap<String, CachedRun>,
    order: VecDeque<String>,
}

impl IdempotencyCache {
    fn new(capacity: usize) -> Self {
        Self {
            capacity,
            values: HashMap::new(),
            order: VecDeque::new(),
        }
    }

    fn get(&self, key: &str) -> Option<&CachedRun> {
        self.values.get(key)
    }

    fn insert(&mut self, key: String, value: CachedRun) {
        if let Some(existing) = self.values.get_mut(&key) {
            *existing = value;
            return;
        }
        while self.values.len() >= self.capacity {
            if let Some(oldest) = self.order.pop_front() {
                self.values.remove(&oldest);
            } else {
                break;
            }
        }
        self.order.push_back(key.clone());
        self.values.insert(key, value);
    }

    fn remove_group(&mut self, group_id: &str) {
        self.values
            .retain(|_, cached| !session_belongs_to_group(&cached.session_key, group_id));
        let values = &self.values;
        self.order.retain(|key| values.contains_key(key));
    }
}

enum InternalEvent {
    RunFinished(String),
    SupervisorAnnouncementFinished {
        session_key: String,
        manager_run_id: String,
    },
    SupervisorTimeout {
        session_key: String,
        generation: u64,
    },
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{
        RuntimeConfig, StateScope, SupervisorAssignment, SupervisorAssignmentMode,
        SupervisorCompletion,
    };

    fn supervisor_behavior() -> BehaviorConfig {
        BehaviorConfig::Supervisor {
            assignment: SupervisorAssignment {
                mode: SupervisorAssignmentMode::EachMember,
                task_template: "{index}/{total} {member}: {input}".to_string(),
            },
            completion: SupervisorCompletion {
                timeout_ms: 1_000,
                max_retries: 0,
            },
            summary_template: "成功 {success_count}，失败 {failed_count}\n{results}\n输入：{input}"
                .to_string(),
        }
    }

    fn instance_config(behavior: BehaviorConfig) -> InstanceConfig {
        InstanceConfig {
            bot: BotProfile {
                source: None,
                profile: "supervisor".to_string(),
                name: "任务协调者".to_string(),
                summary: "规则任务协调者".to_string(),
                domains: "testing".to_string(),
                skills: "supervision".to_string(),
                scopes: None,
                runtime: RuntimeConfig::Rule {
                    response_delay_ms: 0,
                    behavior,
                },
            },
            scopes: "local".to_string(),
            bcs_url: "ws://127.0.0.1/ws/bot".to_string(),
            profile_dir: PathBuf::from("/tmp/rule-bot-test"),
        }
    }

    fn supervisor_input() -> BehaviorInput {
        BehaviorInput {
            session_key: "group:session".to_string(),
            bcs_group_id: "group".to_string(),
            bcs_session_id: Some("group:session".to_string()),
            message_text: "检查发布".to_string(),
            context_message: "检查发布".to_string(),
            task_id: None,
            sender_name: "human".to_string(),
            recipient_role: Some("manager".to_string()),
            group_type: Some("manager_worker".to_string()),
            participants: vec![
                "任务协调者(manager-id)".to_string(),
                "成员A(worker-a)".to_string(),
                "成员B(worker-b)".to_string(),
            ],
        }
    }

    fn take_dispatch(outgoing_rx: &mut mpsc::UnboundedReceiver<BcsFrame>) -> (String, String) {
        match outgoing_rx
            .try_recv()
            .unwrap_or_else(|error| panic!("missing task.dispatch: {error}"))
        {
            BcsFrame::Request(request) => {
                assert_eq!(request.method, "task.dispatch");
                let target = request
                    .params
                    .as_ref()
                    .and_then(|params| params.get("target_bot"))
                    .and_then(|value| value.as_str())
                    .unwrap_or_else(|| panic!("task.dispatch target_bot is missing"))
                    .to_string();
                (request.id, target)
            }
            other => panic!("expected task.dispatch, got {other:?}"),
        }
    }

    fn take_chat_text(outgoing_rx: &mut mpsc::UnboundedReceiver<BcsFrame>) -> String {
        let frame = outgoing_rx
            .try_recv()
            .unwrap_or_else(|error| panic!("missing chat.event: {error}"));
        let BcsFrame::Event(event) = frame else {
            panic!("expected chat.event");
        };
        assert_eq!(event.event, "chat.event");
        event
            .payload
            .as_ref()
            .and_then(|payload| payload.get("message"))
            .and_then(|message| message.get("content"))
            .and_then(|content| content.as_array())
            .and_then(|content| content.first())
            .and_then(|block| block.get("text"))
            .and_then(|text| text.as_str())
            .unwrap_or_else(|| panic!("chat.event text is missing"))
            .to_string()
    }

    #[test]
    fn participant_identity_prefers_uuid_suffix() {
        let identity = parse_participant_identity("成员(worker-id)");
        assert_eq!(identity.name, "成员");
        assert_eq!(identity.target, "worker-id");

        let plain = parse_participant_identity("worker-id");
        assert_eq!(plain.name, "worker-id");
        assert_eq!(plain.target, "worker-id");
        assert_eq!(
            tagged_task("context\n[任务]\n检查上线\n[/任务]\nend").as_deref(),
            Some("检查上线")
        );
        assert!(tagged_task("[SERVICE GROUP CONTEXT]").is_none());
    }

    #[tokio::test]
    async fn cancelled_instance_reports_starting_then_stopped() {
        let behavior = BehaviorConfig::Fixed {
            replies: vec!["你好".to_string()],
            scope: StateScope::Session,
        };
        let config = instance_config(behavior);
        let cancellation = CancellationToken::new();
        cancellation.cancel();
        let (status_tx, mut status_rx) = mpsc::unbounded_channel();

        run_instance(config, cancellation, status_tx)
            .await
            .unwrap_or_else(|error| panic!("cancelled instance should stop cleanly: {error}"));

        assert!(matches!(
            status_rx
                .try_recv()
                .unwrap_or_else(|error| panic!("starting status is missing: {error}")),
            StatusCommand::Update(StatusUpdate {
                state: InstanceState::Starting,
                ..
            })
        ));
        assert!(matches!(
            status_rx
                .try_recv()
                .unwrap_or_else(|error| panic!("stopped status is missing: {error}")),
            StatusCommand::Update(StatusUpdate {
                state: InstanceState::Stopped,
                ..
            })
        ));
        assert!(status_rx.try_recv().is_err());
    }

    #[test]
    fn incoming_dispatch_rejects_invalid_requests_and_ignores_notifications() {
        let behavior = BehaviorConfig::Fixed {
            replies: vec!["你好".to_string()],
            scope: StateScope::Session,
        };
        let config = instance_config(behavior.clone());
        let mut state = RuntimeState::new(&behavior);
        let (outgoing_tx, mut outgoing_rx) = mpsc::unbounded_channel();
        let (internal_tx, _internal_rx) = mpsc::unbounded_channel();
        let cases = [
            ("send", "chat.send", json!({})),
            ("inject", "chat.inject", json!({})),
            ("history", "chat.history", json!({})),
            ("abort", "chat.abort", json!({"run_id": false})),
            ("delete", "session.delete", json!({})),
            ("unknown", "unsupported.method", json!({})),
        ];

        for (id, method, params) in cases {
            handle_incoming(
                &config,
                &mut state,
                BcsFrame::Request(RequestFrame::new(id, method, Some(params))),
                &outgoing_tx,
                &internal_tx,
            )
            .unwrap_or_else(|error| panic!("{method} should return a protocol error: {error}"));
            let BcsFrame::Response(response) = outgoing_rx
                .try_recv()
                .unwrap_or_else(|error| panic!("{method} response is missing: {error}"))
            else {
                panic!("{method} should return a response frame");
            };
            assert_eq!(response.id, id);
            assert!(!response.ok);
            assert!(response.error.is_some());
        }

        for (id, method, params) in [
            ("history-ok", "chat.history", json!({"session_key": "missing"})),
            ("abort-ok", "chat.abort", json!({"session_key": "missing"})),
        ] {
            handle_incoming(
                &config,
                &mut state,
                BcsFrame::Request(RequestFrame::new(id, method, Some(params))),
                &outgoing_tx,
                &internal_tx,
            )
            .unwrap_or_else(|error| panic!("{method} should succeed: {error}"));
            let BcsFrame::Response(response) = outgoing_rx
                .try_recv()
                .unwrap_or_else(|error| panic!("{method} response is missing: {error}"))
            else {
                panic!("{method} should return a response frame");
            };
            assert!(response.ok);
        }

        handle_incoming(
            &config,
            &mut state,
            BcsFrame::Response(ResponseFrame::ok("untracked", json!({}))),
            &outgoing_tx,
            &internal_tx,
        )
        .unwrap_or_else(|error| panic!("untracked response should be ignored: {error}"));
        handle_incoming(
            &config,
            &mut state,
            BcsFrame::Event(bcs_protocol::EventFrame::new("noop", None, None)),
            &outgoing_tx,
            &internal_tx,
        )
        .unwrap_or_else(|error| panic!("notification should be ignored: {error}"));
        assert!(outgoing_rx.try_recv().is_err());
    }

    #[test]
    fn system_supervisor_task_prefers_session_task_and_falls_back_to_group_goal() {
        let goal_only = "\
[SERVICE GROUP CONTEXT]\n\
群组ID: group\n\
会话ID: group:session\n\
模式: manager_worker\n\
你的角色: manager\n\
参与者:\n\
- 名称: 任务协调者 | ID: manager-id | 角色: manager\n\
- 名称: 成员A | ID: worker-a | 角色: worker\n\
\n\
测试协作目标\n\
\n\
[协同提醒] 本群为任务群，你是主 Bot。\n\
[/SERVICE GROUP CONTEXT]";
        assert_eq!(
            supervisor_system_task(goal_only).as_deref(),
            Some("测试协作目标")
        );

        let goal_and_task = "\
[SERVICE GROUP CONTEXT]\n\
参与者:\n\
- 名称: 任务协调者 | ID: manager-id | 角色: manager\n\
\n\
长期协作目标\n\
\n\
[任务]\n\
本次会话任务\n\
[/任务]\n\
\n\
[协同提醒] 本群为任务群，你是主 Bot。\n\
[/SERVICE GROUP CONTEXT]";
        assert_eq!(
            supervisor_system_task(goal_and_task).as_deref(),
            Some("本次会话任务")
        );

        let no_goal = "\
[SERVICE GROUP CONTEXT]\n\
参与者:\n\
- 名称: 任务协调者 | ID: manager-id | 角色: manager\n\
\n\
[协同提醒] 本群为任务群，你是主 Bot。\n\
[/SERVICE GROUP CONTEXT]";
        assert!(supervisor_system_task(no_goal).is_none());
    }

    #[test]
    fn targeted_message_removes_only_the_leading_recipient_mention() {
        let mentions = vec!["bot-id".to_string()];

        assert_eq!(
            strip_leading_recipient_mention(
                "复读机 在吗",
                true,
                Some("复读机"),
                Some("bot-id"),
                &mentions,
            ),
            "在吗"
        );
        assert_eq!(
            strip_leading_recipient_mention(
                "@任务协调者：开始",
                true,
                Some("任务协调者"),
                Some("manager-id"),
                &[],
            ),
            "开始"
        );
        assert_eq!(
            strip_leading_recipient_mention(
                "复读机 在吗",
                false,
                Some("复读机"),
                Some("bot-id"),
                &mentions,
            ),
            "复读机 在吗"
        );
        assert_eq!(
            strip_leading_recipient_mention(
                "复读机今天在吗",
                true,
                Some("复读机"),
                Some("bot-id"),
                &mentions,
            ),
            "复读机今天在吗"
        );
    }

    #[tokio::test]
    async fn supervisor_dispatches_every_worker_and_summarizes_results() {
        let behavior = supervisor_behavior();
        let config = instance_config(behavior.clone());
        let mut state = RuntimeState::new(&behavior);
        state.bot_uuid = Some("manager-id".to_string());
        let input = supervisor_input();
        let (outgoing_tx, mut outgoing_rx) = mpsc::unbounded_channel();
        let (internal_tx, _internal_rx) = mpsc::unbounded_channel();
        let start = match BehaviorRuntime::new(&behavior)
            .handle_send(&input)
            .unwrap_or_else(|error| panic!("supervisor behavior failed: {error}"))
        {
            BehaviorOutcome::StartSupervisor(start) => start,
            BehaviorOutcome::Reply(reply) => panic!("unexpected reply: {reply}"),
        };

        start_supervisor(
            &config,
            &mut state,
            &input,
            "manager-run".to_string(),
            "group".to_string(),
            "group:session\0request".to_string(),
            start,
            &outgoing_tx,
            &internal_tx,
        )
        .unwrap_or_else(|error| panic!("failed to start supervisor: {error}"));

        assert_eq!(
            take_chat_text(&mut outgoing_rx),
            "已收到任务，开始分配给 2 位成员。"
        );
        let (dispatch_a, target_a) = take_dispatch(&mut outgoing_rx);
        let (dispatch_b, target_b) = take_dispatch(&mut outgoing_rx);
        assert_eq!(target_a, "worker-a");
        assert_eq!(target_b, "worker-b");
        handle_bcs_response(
            &mut state,
            ResponseFrame::ok(
                dispatch_a,
                json!({"task_id": "task-a", "status": "dispatched"}),
            ),
            &outgoing_tx,
            &internal_tx,
        )
        .unwrap_or_else(|error| panic!("dispatch response failed: {error}"));
        handle_bcs_response(
            &mut state,
            ResponseFrame::ok(
                dispatch_b,
                json!({"task_id": "task-b", "status": "dispatched"}),
            ),
            &outgoing_tx,
            &internal_tx,
        )
        .unwrap_or_else(|error| panic!("dispatch response failed: {error}"));

        let mut result_a = input.clone();
        result_a.bcs_session_id = Some("wire-result-session".to_string());
        result_a.sender_name = "成员A".to_string();
        result_a.task_id = Some("task-a".to_string());
        result_a.context_message = "A完成".to_string();
        assert!(
            handle_supervisor_result(&mut state, &result_a, &outgoing_tx, &internal_tx)
                .unwrap_or_else(|error| panic!("worker result failed: {error}"))
        );
        assert!(outgoing_rx.try_recv().is_err());

        let mut result_b = input;
        result_b.bcs_session_id = Some("wire-result-session".to_string());
        result_b.sender_name = "成员B".to_string();
        result_b.task_id = Some("task-b".to_string());
        result_b.context_message = "B完成".to_string();
        assert!(
            handle_supervisor_result(&mut state, &result_b, &outgoing_tx, &internal_tx)
                .unwrap_or_else(|error| panic!("worker result failed: {error}"))
        );

        let complete = outgoing_rx
            .try_recv()
            .unwrap_or_else(|error| panic!("missing task.complete: {error}"));
        let final_event = outgoing_rx
            .try_recv()
            .unwrap_or_else(|error| panic!("missing final event: {error}"));
        match complete {
            BcsFrame::Request(request) => assert_eq!(request.method, "task.complete"),
            other => panic!("expected task.complete, got {other:?}"),
        }
        let final_json = serde_json::to_string(&final_event)
            .unwrap_or_else(|error| panic!("failed to serialize final event: {error}"));
        assert!(final_json.contains("成功 2，失败 0"));
        assert!(final_json.contains("成员A [completed]: A完成"));
        assert!(final_json.contains("成员B [completed]: B完成"));
        assert!(final_json.contains("\"bcs_group_id\":\"group:session\""));
        assert!(!state.supervisions.contains_key("group:session"));
    }

    #[tokio::test]
    async fn supervisor_dispatches_group_goal_from_initial_system_context() {
        let behavior = supervisor_behavior();
        let config = instance_config(behavior.clone());
        let mut state = RuntimeState::new(&behavior);
        state.bot_uuid = Some("manager-id".to_string());
        let mut input = supervisor_input();
        input.sender_name = "bcs-system-message".to_string();
        input.message_text = "\
[SERVICE GROUP CONTEXT]\n\
参与者:\n\
- 名称: 任务协调者 | ID: manager-id | 角色: manager\n\
- 名称: 成员A | ID: worker-a | 角色: worker\n\
- 名称: 成员B | ID: worker-b | 角色: worker\n\
\n\
初始化协作目标\n\
\n\
[协同提醒] 本群为任务群，你是主 Bot。\n\
[/SERVICE GROUP CONTEXT]"
            .to_string();
        let (outgoing_tx, mut outgoing_rx) = mpsc::unbounded_channel();
        let (internal_tx, _internal_rx) = mpsc::unbounded_channel();
        let start = match BehaviorRuntime::new(&behavior)
            .handle_send(&input)
            .unwrap_or_else(|error| panic!("supervisor behavior failed: {error}"))
        {
            BehaviorOutcome::StartSupervisor(start) => start,
            BehaviorOutcome::Reply(reply) => panic!("unexpected reply: {reply}"),
        };

        start_supervisor(
            &config,
            &mut state,
            &input,
            "manager-run".to_string(),
            "group".to_string(),
            "group:session\0request".to_string(),
            start,
            &outgoing_tx,
            &internal_tx,
        )
        .unwrap_or_else(|error| panic!("failed to start supervisor: {error}"));

        assert_eq!(
            take_chat_text(&mut outgoing_rx),
            "已收到任务，开始分配给 2 位成员。"
        );
        for _ in 0..2 {
            let dispatch = outgoing_rx
                .try_recv()
                .unwrap_or_else(|error| panic!("missing task.dispatch: {error}"));
            let BcsFrame::Request(request) = dispatch else {
                panic!("expected task.dispatch request");
            };
            assert_eq!(request.method, "task.dispatch");
            let message = request
                .params
                .as_ref()
                .and_then(|params| params.get("message"))
                .and_then(|value| value.as_str())
                .unwrap_or_else(|| panic!("task.dispatch message is missing"));
            assert!(message.contains("初始化协作目标"));
        }
        assert!(state.supervisions.contains_key("group:session"));
    }

    #[tokio::test]
    async fn supervisor_timeout_finishes_without_retry() {
        let behavior = supervisor_behavior();
        let config = instance_config(behavior.clone());
        let mut state = RuntimeState::new(&behavior);
        state.bot_uuid = Some("manager-id".to_string());
        let input = supervisor_input();
        let (outgoing_tx, mut outgoing_rx) = mpsc::unbounded_channel();
        let (internal_tx, _internal_rx) = mpsc::unbounded_channel();
        let start = match BehaviorRuntime::new(&behavior)
            .handle_send(&input)
            .unwrap_or_else(|error| panic!("supervisor behavior failed: {error}"))
        {
            BehaviorOutcome::StartSupervisor(start) => start,
            BehaviorOutcome::Reply(reply) => panic!("unexpected reply: {reply}"),
        };
        start_supervisor(
            &config,
            &mut state,
            &input,
            "manager-run".to_string(),
            "group".to_string(),
            "group:session\0request".to_string(),
            start,
            &outgoing_tx,
            &internal_tx,
        )
        .unwrap_or_else(|error| panic!("failed to start supervisor: {error}"));
        assert_eq!(
            take_chat_text(&mut outgoing_rx),
            "已收到任务，开始分配给 2 位成员。"
        );
        let _ = take_dispatch(&mut outgoing_rx);
        let _ = take_dispatch(&mut outgoing_rx);

        handle_supervisor_timeout(&mut state, "group:session", 1, &outgoing_tx, &internal_tx)
            .unwrap_or_else(|error| panic!("timeout handling failed: {error}"));

        let complete = outgoing_rx
            .try_recv()
            .unwrap_or_else(|error| panic!("missing task.complete: {error}"));
        let final_event = outgoing_rx
            .try_recv()
            .unwrap_or_else(|error| panic!("missing final event: {error}"));
        assert!(matches!(complete, BcsFrame::Request(_)));
        let final_json = serde_json::to_string(&final_event)
            .unwrap_or_else(|error| panic!("failed to serialize final event: {error}"));
        assert!(final_json.contains("成功 0，失败 2"));
        assert!(final_json.contains("[timeout]"));
    }

    #[tokio::test]
    async fn supervisor_visible_reply_uses_configured_delay() {
        let behavior = supervisor_behavior();
        let mut config = instance_config(behavior.clone());
        config.bot.runtime = RuntimeConfig::Rule {
            response_delay_ms: 60_000,
            behavior: behavior.clone(),
        };
        let mut state = RuntimeState::new(&behavior);
        state.bot_uuid = Some("manager-id".to_string());
        let mut input = supervisor_input();
        input.sender_name = "bcs-system-message".to_string();
        input.message_text = "[MANAGER-WORKER GROUP CONTEXT]".to_string();
        let (outgoing_tx, mut outgoing_rx) = mpsc::unbounded_channel();
        let (internal_tx, _internal_rx) = mpsc::unbounded_channel();
        let start = match BehaviorRuntime::new(&behavior)
            .handle_send(&input)
            .unwrap_or_else(|error| panic!("supervisor behavior failed: {error}"))
        {
            BehaviorOutcome::StartSupervisor(start) => start,
            BehaviorOutcome::Reply(reply) => panic!("unexpected reply: {reply}"),
        };

        start_supervisor(
            &config,
            &mut state,
            &input,
            "manager-run".to_string(),
            "group".to_string(),
            "group:session\0request".to_string(),
            start,
            &outgoing_tx,
            &internal_tx,
        )
        .unwrap_or_else(|error| panic!("failed to start supervisor: {error}"));

        assert!(outgoing_rx.try_recv().is_err());
        assert!(state.pending_runs.contains_key("manager-run"));
        for (_, pending) in state.pending_runs.drain() {
            pending.cancellation.cancel();
        }
    }

    #[tokio::test]
    async fn supervisor_waits_for_delayed_announcement_before_dispatching() {
        let behavior = supervisor_behavior();
        let mut config = instance_config(behavior.clone());
        config.bot.runtime = RuntimeConfig::Rule {
            response_delay_ms: 10,
            behavior: behavior.clone(),
        };
        let mut state = RuntimeState::new(&behavior);
        state.bot_uuid = Some("manager-id".to_string());
        let input = supervisor_input();
        let (outgoing_tx, mut outgoing_rx) = mpsc::unbounded_channel();
        let (internal_tx, mut internal_rx) = mpsc::unbounded_channel();
        let start = match BehaviorRuntime::new(&behavior)
            .handle_send(&input)
            .unwrap_or_else(|error| panic!("supervisor behavior failed: {error}"))
        {
            BehaviorOutcome::StartSupervisor(start) => start,
            BehaviorOutcome::Reply(reply) => panic!("unexpected reply: {reply}"),
        };

        start_supervisor(
            &config,
            &mut state,
            &input,
            "manager-run".to_string(),
            "group".to_string(),
            "group:session\0request".to_string(),
            start,
            &outgoing_tx,
            &internal_tx,
        )
        .unwrap_or_else(|error| panic!("failed to start supervisor: {error}"));

        assert!(outgoing_rx.try_recv().is_err());
        assert!(state.dispatch_requests.is_empty());
        let event = tokio::time::timeout(Duration::from_secs(1), internal_rx.recv())
            .await
            .unwrap_or_else(|_| panic!("announcement did not finish"))
            .unwrap_or_else(|| panic!("internal channel closed"));
        let InternalEvent::SupervisorAnnouncementFinished {
            session_key,
            manager_run_id,
        } = event
        else {
            panic!("unexpected internal event");
        };
        state.pending_runs.remove(&manager_run_id);
        begin_supervisor_dispatch(
            &mut state,
            &session_key,
            &manager_run_id,
            &outgoing_tx,
            &internal_tx,
        )
        .unwrap_or_else(|error| panic!("failed to dispatch after announcement: {error}"));

        assert_eq!(
            take_chat_text(&mut outgoing_rx),
            "已收到任务，开始分配给 2 位成员。"
        );
        let _ = take_dispatch(&mut outgoing_rx);
        let _ = take_dispatch(&mut outgoing_rx);
    }

    #[test]
    fn inject_does_not_advance_fixed_behavior() {
        let behavior = BehaviorConfig::Fixed {
            replies: vec!["第一条".to_string(), "第二条".to_string()],
            scope: StateScope::Session,
        };
        let mut state = RuntimeState::new(&behavior);
        let (outgoing_tx, mut outgoing_rx) = mpsc::unbounded_channel();
        let inject = RequestFrame::new(
            "inject",
            "chat.inject",
            Some(json!({
                "session_key": "session",
                "bcs_group_id": "group",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "静默消息"}],
                    "timestamp": 1
                },
                "channel": {"source": "api"},
                "session_context": {
                    "session_id": "session",
                    "participants": [],
                    "originator": "human",
                    "from": "human",
                    "message": "静默消息"
                }
            })),
        );
        handle_chat_inject(&mut state, inject, &outgoing_tx)
            .unwrap_or_else(|error| panic!("inject failed: {error}"));
        assert!(matches!(
            outgoing_rx
                .try_recv()
                .unwrap_or_else(|error| panic!("missing inject ACK: {error}")),
            BcsFrame::Response(_)
        ));

        let reply = state
            .behavior
            .handle_send(&BehaviorInput {
                session_key: "session".to_string(),
                bcs_group_id: "group".to_string(),
                bcs_session_id: None,
                message_text: "正常消息".to_string(),
                context_message: "正常消息".to_string(),
                task_id: None,
                sender_name: "human".to_string(),
                recipient_role: None,
                group_type: None,
                participants: Vec::new(),
            })
            .unwrap_or_else(|error| panic!("fixed behavior failed: {error}"));
        assert!(matches!(reply, BehaviorOutcome::Reply(value) if value == "第一条"));
    }

    #[test]
    fn session_delete_cancels_only_matching_group_runs_and_cache() {
        let behavior = BehaviorConfig::Fixed {
            replies: vec!["你好".to_string()],
            scope: StateScope::Session,
        };
        let mut state = RuntimeState::new(&behavior);
        state
            .history
            .insert("group-a:session".to_string(), Vec::new());
        state
            .history
            .insert("group-b:session".to_string(), Vec::new());

        let deleted_run = CancellationToken::new();
        let retained_run = CancellationToken::new();
        state.pending_runs.insert(
            "run-a".to_string(),
            PendingRun {
                session_key: "group-a:session".to_string(),
                cancellation: deleted_run.clone(),
            },
        );
        state.pending_runs.insert(
            "run-b".to_string(),
            PendingRun {
                session_key: "group-b:session".to_string(),
                cancellation: retained_run.clone(),
            },
        );
        state.idempotency.insert(
            "group-a:session\0request".to_string(),
            CachedRun {
                run_id: "run-a".to_string(),
                group_id: "group-a".to_string(),
                session_key: "group-a:session".to_string(),
                reply: Some("你好".to_string()),
            },
        );
        state.idempotency.insert(
            "group-b:session\0request".to_string(),
            CachedRun {
                run_id: "run-b".to_string(),
                group_id: "group-b".to_string(),
                session_key: "group-b:session".to_string(),
                reply: Some("你好".to_string()),
            },
        );

        let (outgoing_tx, mut outgoing_rx) = mpsc::unbounded_channel();
        handle_session_delete(
            &mut state,
            RequestFrame::new(
                "delete",
                "session.delete",
                Some(json!({"bcs_group_id": "group-a"})),
            ),
            &outgoing_tx,
        )
        .unwrap_or_else(|error| panic!("session delete failed: {error}"));

        assert!(matches!(
            outgoing_rx
                .try_recv()
                .unwrap_or_else(|error| panic!("missing delete ACK: {error}")),
            BcsFrame::Response(_)
        ));
        assert!(deleted_run.is_cancelled());
        assert!(!retained_run.is_cancelled());
        assert!(!state.pending_runs.contains_key("run-a"));
        assert!(state.pending_runs.contains_key("run-b"));
        assert!(!state.history.contains_key("group-a:session"));
        assert!(state.history.contains_key("group-b:session"));
        assert!(
            !state
                .idempotency
                .values
                .contains_key("group-a:session\0request")
        );
        assert!(
            state
                .idempotency
                .values
                .contains_key("group-b:session\0request")
        );
    }

    #[tokio::test]
    async fn duplicate_request_does_not_accelerate_pending_reply() {
        let behavior = BehaviorConfig::Fixed {
            replies: vec!["你好".to_string(), "再见".to_string()],
            scope: StateScope::Session,
        };
        let mut config = instance_config(behavior.clone());
        config.bot.runtime = RuntimeConfig::Rule {
            response_delay_ms: 60_000,
            behavior: behavior.clone(),
        };
        let mut state = RuntimeState::new(&behavior);
        let (outgoing_tx, mut outgoing_rx) = mpsc::unbounded_channel();
        let (internal_tx, _internal_rx) = mpsc::unbounded_channel();
        let params = json!({
            "session_key": "session",
            "bcs_group_id": "group",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "消息"}],
                "timestamp": 1
            },
            "channel": {"source": "api"},
            "session_context": {
                "session_id": "session",
                "participants": [],
                "originator": "human",
                "from": "human",
                "message": "消息"
            },
            "idempotency_key": "same-request"
        });

        handle_chat_send(
            &config,
            &mut state,
            RequestFrame::new("first", "chat.send", Some(params.clone())),
            &outgoing_tx,
            &internal_tx,
        )
        .unwrap_or_else(|error| panic!("first send failed: {error}"));
        assert!(matches!(
            outgoing_rx
                .try_recv()
                .unwrap_or_else(|error| panic!("missing first ACK: {error}")),
            BcsFrame::Response(_)
        ));
        assert!(state.pending_runs.contains_key("first"));

        handle_chat_send(
            &config,
            &mut state,
            RequestFrame::new("duplicate", "chat.send", Some(params)),
            &outgoing_tx,
            &internal_tx,
        )
        .unwrap_or_else(|error| panic!("duplicate send failed: {error}"));
        assert!(matches!(
            outgoing_rx
                .try_recv()
                .unwrap_or_else(|error| panic!("missing duplicate ACK: {error}")),
            BcsFrame::Response(_)
        ));
        assert!(outgoing_rx.try_recv().is_err());
        assert_eq!(state.pending_runs.len(), 1);
        assert!(state.pending_runs.contains_key("first"));

        for (_, pending) in state.pending_runs.drain() {
            pending.cancellation.cancel();
        }
    }
}
