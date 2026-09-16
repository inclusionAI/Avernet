#[path = "task_flow/support.rs"]
mod support;
pub(crate) use support::{apply_session_participants};
use support::*;
#[path = "task_flow/dispatch.rs"]
mod dispatch;
pub use dispatch::handle_task_dispatch;

use std::collections::BTreeMap;

use bcs_domain::{LedgerSummary, MessageAudience, MessageVisibilityDomain, SenderType};
use bcs_protocol::{BcsFrame, GroupContext, RequestFrame};
use bcs_service_api::{
    BotDeliveryCommand, BotDeliveryKind, ChatResponseMode, DeliveryType, FrontendDeliveryCommand,
    FrontendDeliveryKind, FrontendDeliveryResult, FrontendDeliveryTarget, Group, GroupStatus,
    GroupStrategy, MESSAGE_LOG_SCHEMA_VERSION, MessageLogContent, MessageLogEventType,
    MessageLogMode, MessageLogStatus, Participant, ParticipantMode, ParticipantRole, ServiceError,
    ServiceResult, Session, SessionKind, SessionStatus, SystemMessageEvent, TaskCompleteCommand,
    TaskCompleteOutcome, TaskDispatchCommand, TaskDispatchOutcome, TaskMessageCommand,
    TaskMessageOutcome,
    port::NewEvent,
    types::{EVENT_SCHEMA_VERSION_V1, EventScope, EventSubject},
};
use chrono::{SecondsFormat, TimeZone, Utc};
use serde_json::Value;
use tracing::{info, warn};

use crate::BcsMessageFlow;
use crate::MSG_LOG_TARGET;
use crate::protocol_context::group_type_wire;
use crate::task_store::{TaskEntry, new_task_entry};

async fn record_task_event(
    flow: &BcsMessageFlow,
    event_type: &str,
    entry: &TaskEntry,
    actor_id: &str,
    occurred_at_ms: u64,
    data: BTreeMap<String, Value>,
) -> ServiceResult<()> {
    let Some(recorder) = flow.event_recorder.as_ref() else {
        return Ok(());
    };
    let occurred_at = Utc
        .timestamp_millis_opt(i64::try_from(occurred_at_ms).map_err(|_| {
            ServiceError::InternalError("Task Event timestamp is out of range".to_string())
        })?)
        .single()
        .ok_or_else(|| ServiceError::InternalError("Task Event timestamp is invalid".to_string()))?
        .to_rfc3339_opts(SecondsFormat::Millis, true);
    recorder
        .record(NewEvent {
            event_id: format!("evt_{}", uuid::Uuid::new_v4()),
            event_type: event_type.to_string(),
            schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
            producer: "bcs-manager-worker".to_string(),
            producer_key: format!("{event_type}:{}", entry.task_id),
            occurred_at,
            subject: EventSubject {
                subject_type: "task".to_string(),
                id: entry.task_id.clone(),
            },
            scope: EventScope {
                group_id: Some(entry.group_id.clone()),
                session_id: entry.session_id.clone(),
                task_id: Some(entry.task_id.clone()),
                ..EventScope::default()
            },
            stream_key: format!("task:{}", entry.task_id),
            actor: Some(crate::bot_event_actor(actor_id)),
            correlation_id: Some(entry.task_id.clone()),
            causation_event_id: None,
            trace_id: None,
            data,
        })
        .await
        .map_err(|error| ServiceError::InternalError(error.to_string()))?;
    Ok(())
}

pub(crate) async fn record_task_completed(
    flow: &BcsMessageFlow,
    entry: &TaskEntry,
    result: &str,
    completed_at_ms: u64,
) -> ServiceResult<()> {
    let result_bytes = result.len();
    let completed_at = Utc
        .timestamp_millis_opt(i64::try_from(completed_at_ms).map_err(|_| {
            ServiceError::InternalError("Task completion timestamp is out of range".to_string())
        })?)
        .single()
        .ok_or_else(|| {
            ServiceError::InternalError("Task completion timestamp is invalid".to_string())
        })?
        .to_rfc3339_opts(SecondsFormat::Millis, true);
    let mut data = BTreeMap::from([
        ("task_id".to_string(), serde_json::json!(entry.task_id)),
        (
            "manager_id".to_string(),
            serde_json::json!(entry.driver_bot),
        ),
        ("worker_id".to_string(), serde_json::json!(entry.target_bot)),
        (
            "result".to_string(),
            serde_json::json!({
                "content_type": "text/plain",
                "size_bytes": result_bytes,
                "text": result,
                "truncated": false
            }),
        ),
        ("completed_at".to_string(), serde_json::json!(completed_at)),
    ]);
    if let Some(session_id) = entry.session_id.as_ref() {
        data.insert("session_id".to_string(), serde_json::json!(session_id));
    }
    record_task_event(
        flow,
        "task.completed",
        entry,
        &entry.target_bot,
        completed_at_ms,
        data,
    )
    .await
}

async fn emit_unknown_task_target_notice(
    flow: &BcsMessageFlow,
    group: &Group,
    group_id: &str,
    session_id: Option<&str>,
    requested_target: &str,
) {
    let Some(system_message) = flow.system_message.as_ref() else {
        return;
    };
    let available_workers = group
        .participants
        .iter()
        .filter(|participant| participant.role == ParticipantRole::Worker)
        .map(|participant| {
            participant
                .bot_name
                .as_deref()
                .map(str::trim)
                .filter(|name| !name.is_empty())
                .map(|name| format!("{} ({})", name, participant.bot_uuid))
                .unwrap_or_else(|| participant.bot_uuid.clone())
        })
        .collect::<Vec<_>>()
        .join(", ");
    let available_workers = if available_workers.is_empty() {
        "无".to_string()
    } else {
        available_workers
    };
    let message = format!(
        "[协同提醒] 未找到 worker {:?}，任务未派发。当前可用 worker: {}。请使用准确名称或 Bot ID 重试。",
        requested_target, available_workers
    );
    let event = SystemMessageEvent::GenericNotification {
        group_id: group_id.to_string(),
        message,
        receivers: Vec::new(),
    };
    let notify_session_id = session_id.unwrap_or(group_id);
    if let Err(error) = system_message
        .notify(group_id, event, notify_session_id, &group.participants)
        .await
    {
        warn!(
            %group_id,
            %notify_session_id,
            error = %error,
            "failed to emit unknown task target notice"
        );
    }
}

pub(crate) async fn emit_task_ledger_status(
    flow: &BcsMessageFlow,
    group: &Group,
    group_id: &str,
    session_id: Option<&str>,
    driver_bot_id: &str,
) {
    let Some(system_message) = flow.system_message.as_ref() else {
        return;
    };
    let Some(receiver) = group
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == driver_bot_id)
        .cloned()
    else {
        return;
    };
    let summary = flow
        .task_store
        .ledger_summary_at(group_id, session_id, now_ms())
        .await;
    let message = format_ledger_status_line(&summary);
    if message.is_empty() {
        return;
    }
    let event = SystemMessageEvent::GenericNotification {
        group_id: group_id.to_string(),
        message,
        receivers: vec![receiver],
    };
    let notify_session_id = session_id.unwrap_or(group_id);
    let _ = system_message
        .notify(group_id, event, notify_session_id, &group.participants)
        .await;
}

fn format_ledger_status_line(summary: &LedgerSummary) -> String {
    if summary.pending.is_empty()
        && summary.replied.is_empty()
        && summary.failed.is_empty()
        && summary.timed_out.is_empty()
    {
        return String::new();
    }
    format!(
        "[任务状态] 待回复: {} | 已回复: {} | 失败: {} | 超时: {}",
        join_or_dash(&summary.pending),
        join_or_dash(&summary.replied),
        join_or_dash(&summary.failed),
        join_or_dash(&summary.timed_out),
    )
}

fn join_or_dash(items: &[String]) -> String {
    if items.is_empty() {
        "-".to_string()
    } else {
        items.join(", ")
    }
}

pub async fn handle_task_complete(
    flow: &BcsMessageFlow,
    cmd: TaskCompleteCommand,
) -> ServiceResult<TaskCompleteOutcome> {
    let raw_group_id = cmd
        .payload
        .get("group_id")
        .and_then(|value| value.as_str())
        .unwrap_or(&cmd.task_id);
    let status = cmd
        .payload
        .get("status")
        .and_then(|value| value.as_str())
        .unwrap_or("completed");
    let group_status = match status {
        "completed" => GroupStatus::Completed,
        "error" => GroupStatus::Error,
        other => {
            return Err(ServiceError::InvalidOperation {
                message: format!("invalid task complete status: {other}"),
                request_id: Some(cmd.task_id),
            });
        }
    };

    let scope = resolve_task_complete_scope(flow, raw_group_id, &cmd.payload).await?;
    let group = flow
        .group
        .get(&scope.group_id)
        .await
        .ok_or_else(|| ServiceError::GroupNotFound(scope.group_id.clone()))?;
    ensure_task_dispatch_allowed(&group)?;
    if !is_task_manager(&group, &cmd.bot_id) {
        return Err(ServiceError::Unauthorized(
            task_manager_error_message(&group, "complete a task group").to_string(),
        ));
    }
    let queued = crate::queued_task::refresh_completion_scope(flow, &scope.group_id, scope.session_id.as_deref()).await?;
    let mut pending = flow
        .task_store
        .pending_targets_at(&scope.group_id, scope.session_id.as_deref(), now_ms())
        .await;
    for row in queued {
        if let Some(intent) = crate::queued_task::intent(&row)? {
            if scope.session_id.is_none() && matches!(row.state.status,
                bcs_domain::message_delivery::MessageDeliveryStatus::Queued
                | bcs_domain::message_delivery::MessageDeliveryStatus::Dispatching
                | bcs_domain::message_delivery::MessageDeliveryStatus::Running
                | bcs_domain::message_delivery::MessageDeliveryStatus::Unknown
                | bcs_domain::message_delivery::MessageDeliveryStatus::Cancelling
                | bcs_domain::message_delivery::MessageDeliveryStatus::CancelUnknown) {
                pending.push(format!("{} (task pending in {})", row.target_bot_id, row.session_id));
            }
            // An active Manager result may itself issue task.complete. Only
            // queued/uncertain return legs block it; its own active run does not.
            if intent.leg != crate::queued_task::TaskLeg::Dispatch && matches!(row.state.status,
                bcs_domain::message_delivery::MessageDeliveryStatus::Queued
                | bcs_domain::message_delivery::MessageDeliveryStatus::Unknown
                | bcs_domain::message_delivery::MessageDeliveryStatus::Cancelling
                | bcs_domain::message_delivery::MessageDeliveryStatus::CancelUnknown) {
                pending.push(format!("{} (task result pending)", row.target_bot_id));
            }
        }
    }
    pending.sort();
    pending.dedup();
    if !pending.is_empty() {
        if cmd.via_echo {
            return Ok(TaskCompleteOutcome {
                status: status.to_string(),
                blocked: true,
                pending,
                callback_requested: false,
                completed_session: None,
                frontend_deliveries: Vec::new(),
            });
        }
        return Err(ServiceError::InvalidOperation {
            message: format!(
                "task completion blocked by pending targets: {}",
                pending.join(", ")
            ),
            request_id: Some(cmd.task_id),
        });
    }

    let completed_session = if let Some(session_id) = scope.session_id.as_deref() {
        complete_session_target(flow, &scope.group_id, session_id, status, &cmd.payload).await?
    } else {
        crate::update_group_status(
            flow.group.as_ref(),
            &scope.group_id,
            group_status,
            format!("task_{status}"),
            crate::bot_event_actor(&cmd.bot_id),
        )
        .await?;
        complete_service_session_if_needed(flow, &group, &scope.group_id, status, &cmd.payload)
            .await?
    };
    log_task_complete(
        &scope.group_id,
        scope.session_id.as_deref(),
        &cmd.task_id,
        &cmd.bot_id,
        status,
    );

    Ok(TaskCompleteOutcome {
        status: status.to_string(),
        blocked: false,
        pending: Vec::new(),
        callback_requested: completed_session.is_some(),
        completed_session,
        frontend_deliveries: Vec::new(),
    })
}

pub async fn handle_task_message(
    flow: &BcsMessageFlow,
    cmd: TaskMessageCommand,
) -> ServiceResult<TaskMessageOutcome> {
    let (group_id, manager_session_id) = task_dispatch_scope(&cmd.group_id, &cmd.payload);
    let manager_session_id = manager_session_id.ok_or_else(|| ServiceError::InvalidOperation {
        message: "task.message requires bcs_session_id".to_string(),
        request_id: None,
    })?;
    let mut group = flow
        .group
        .get(&group_id)
        .await
        .ok_or_else(|| ServiceError::GroupNotFound(group_id.clone()))?;
    apply_session_participants(flow, &mut group, &group_id, &manager_session_id).await?;

    if group.group_strategy != GroupStrategy::ManagerWorker {
        return Err(ServiceError::InvalidOperation {
            message: "task.message requires manager_worker group".to_string(),
            request_id: None,
        });
    }
    if !is_task_worker(&group, &cmd.worker_bot_id) {
        return Err(ServiceError::Unauthorized(
            "only worker bot can send task messages".to_string(),
        ));
    }

    let manager = group
        .participants
        .iter()
        .find(|participant| participant.role == ParticipantRole::Manager)
        .cloned()
        .ok_or_else(|| ServiceError::InvalidOperation {
            message: "manager bot not found".to_string(),
            request_id: None,
        })?;
    let manager_mode = manager
        .mode
        .unwrap_or_else(|| ParticipantMode::default_for(manager.actor_kind));
    if manager_mode == ParticipantMode::Muted {
        return Err(ServiceError::InvalidOperation {
            message: "manager bot is muted".to_string(),
            request_id: None,
        });
    }

    let message = task_message(&cmd.payload);
    if message.trim().is_empty() {
        return Err(ServiceError::InvalidOperation {
            message: "task.message requires non-empty message".to_string(),
            request_id: None,
        });
    }
    let worker = group
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == cmd.worker_bot_id)
        .ok_or_else(|| {
            ServiceError::Unauthorized("only worker bot can send task messages".to_string())
        })?;
    let worker_name = resolve_participant_name(flow, worker).await;
    let manager_name = resolve_participant_name(flow, &manager).await;
    let run_id = uuid::Uuid::new_v4().to_string();
    if let Some(drain) = crate::queued_task::admission_mode(flow, &group, &manager_session_id, &manager.bot_uuid).await? {
        let intent = crate::queued_task::TaskIntent { leg:crate::queued_task::TaskLeg::Message,
            task_id:run_id, manager:manager.bot_uuid.clone(), worker:cmd.worker_bot_id.clone(),
            worker_name:worker_name.clone(), response_mode:ChatResponseMode::Full };
        let admission = crate::queued_task::command(flow, &group, &manager_session_id, intent,
            &message, cmd.payload.get("attachments"), &worker_name, drain).await?;
        crate::queued_task::admit(flow, admission).await?;
        let frontend_deliveries = publish_task_message_to_workbench(flow, &group, &manager_session_id,
            &cmd.worker_bot_id, &worker_name, &message).await;
        return Ok(TaskMessageOutcome { status:"queued".into(), bot_deliveries:Vec::new(), frontend_deliveries });
    }
    let delivery_target = match flow
        .registry
        .resolve_delivery_target(&manager.bot_uuid)
        .await
    {
        Ok(target) => target,
        Err(error) => {
            let error_text = error.to_string();
            log_manager_worker_deliver_result(
                &group_id,
                Some(&manager_session_id),
                &run_id,
                &manager.bot_uuid,
                Some(&cmd.worker_bot_id),
                DeliveryType::Send,
                false,
                Some(error_text.as_str()),
                Some("resolve_target"),
            );
            return Err(error);
        }
    };
    let provider_tags = if delivery_target.is_http_provider() {
        manager.tags.as_slice()
    } else {
        &[]
    };
    let frame = build_task_message_frame(
        &group,
        &manager_session_id,
        &cmd.worker_bot_id,
        &worker_name,
        &manager.bot_uuid,
        &manager_name,
        &message,
        &run_id,
        provider_tags,
    );
    let delivery_kind = BotDeliveryKind::TaskMessage;
    flow.register_send_context(
        DeliveryType::Send,
        &delivery_target,
        &frame,
        &run_id,
        &manager.bot_uuid,
        &group_id,
        Some(&manager_session_id),
        &[],
    )
    .await?;
    let delivery = flow
        .bot_delivery
        .deliver(BotDeliveryCommand {
            target: delivery_target,
            run_id: run_id.clone(),
            frame,
            delivery_kind,
            provider_transport: Default::default(),
            provider_bypass_headers: Vec::new(),
        })
        .await;
    let result = match delivery {
        Ok(result) => result,
        Err(error) => {
            flow.discard_send_context(&run_id).await?;
            return Err(error);
        }
    };

    log_manager_worker_deliver_result(
        &group_id,
        Some(&manager_session_id),
        &run_id,
        &manager.bot_uuid,
        Some(&cmd.worker_bot_id),
        DeliveryType::Send,
        result.delivered,
        result.error.as_ref().map(ToString::to_string).as_deref(),
        if result.delivered {
            None
        } else {
            Some("deliver")
        },
    );

    if !result.delivered {
        flow.discard_send_context(&run_id).await?;
        return Err(ServiceError::InvalidOperation {
            message: "manager bot is not connected".to_string(),
            request_id: Some(run_id),
        });
    }
    crate::group_flow::try_persist_group_message(
        flow,
        &group_id,
        Some(&manager_session_id),
        &cmd.worker_bot_id,
        SenderType::Bot,
        "chat",
        Value::String(message.clone()),
        None,
        None,
        &run_id,
    )
    .await?;
    let frontend_deliveries = publish_task_message_to_workbench(
        flow,
        &group,
        &manager_session_id,
        &cmd.worker_bot_id,
        &worker_name,
        &message,
    )
    .await;

    Ok(TaskMessageOutcome {
        status: "sent".to_string(),
        bot_deliveries: vec![result],
        frontend_deliveries,
    })
}
