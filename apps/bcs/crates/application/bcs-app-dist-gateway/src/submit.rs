//! Admission use cases: a user message, a reply to an open question, a cancel.

use crate::error::{admit_error, core_error};
use crate::identity::*;
use crate::{DistGateway, invalid, scope_of};
use bcs_dist_model::*;
use bcs_service_api::application::dist_gateway::*;
use bcs_service_api::core::dist_gateway::{AdmitTaskCommand, CoreAdmission, DistGatewayCoreError};
use bcs_service_api::port::dist_agent::GroupDirectoryError;
use serde_json::json;

const SCHEMA_VERSION: u32 = 1;

pub(crate) async fn submit(
    gateway: &DistGateway,
    caller: &GatewayCaller,
    request: SubmitInput,
) -> Result<AdmissionReceipt, GatewayError> {
    let scope = scope_of(caller)?;
    check_request_id(&request.message_id)?;
    if request.group.0.is_empty() {
        return Err(invalid("group_missing"));
    }
    let command = match (&request.session, &request.task) {
        (_, Some(tid)) => reply(gateway, &scope, &request, tid).await?,
        (Some(sid), None) => {
            parse_session(sid)?;
            gateway.check_group(&scope, sid, &request.group).await?;
            new_task(gateway, &scope, &request, sid.clone(), None)
        }
        (None, None) => {
            let sid = session_id(&scope, &request.group, &request.message_id, gateway.config.shard_count);
            let spec = new_spec(gateway, &scope, &request.group, &sid).await?;
            new_task(gateway, &scope, &request, sid, Some(spec))
        }
    };
    let CoreAdmission(receipt) = gateway
        .core
        .admit(&scope, command)
        .await
        .map_err(|e| admit_error(e, &request.message_id))?;
    Ok(receipt)
}

pub(crate) async fn cancel(
    gateway: &DistGateway,
    caller: &GatewayCaller,
    request: CancelTask,
) -> Result<AdmissionReceipt, GatewayError> {
    let scope = scope_of(caller)?;
    check_request_id(&request.request_id)?;
    let sid = session_of_task(&request.tid)?;
    gateway.check_group(&scope, &sid, &request.group).await?;
    let body = InputBody::Cancel {
        reason: request.reason.clone(),
    };
    let command = AdmitTaskCommand {
        input: InputEnvelope {
            schema_version: SCHEMA_VERSION,
            input_id: cancel_input_id(&scope, &request.tid, &sid, &request.request_id),
            business_digest: content_digest(&json!({
                "kind": "cancel",
                "tid": request.tid.0,
                "reason": request.reason,
            })),
            sid,
            tid: request.tid.clone(),
            body,
        },
        create_session: None,
        create_task: None,
    };
    match gateway.core.admit(&scope, command).await {
        Ok(CoreAdmission(receipt)) => Ok(receipt),
        // The only state a cancel is refused in: the task is finished.
        Err(DistGatewayCoreError::InvalidTransition) => Err(GatewayError::NotCancelable),
        Err(error) => Err(admit_error(error, &request.request_id)),
    }
}

/// Freezes the group for a new session.
async fn new_spec(
    gateway: &DistGateway,
    scope: &TaskScope,
    group: &GroupId,
    sid: &Sid,
) -> Result<SessionSpec, GatewayError> {
    let frozen = gateway.groups.resolve(scope, group).await.map_err(|e| match e {
        GroupDirectoryError::NotFound => GatewayError::NotFound,
        GroupDirectoryError::Unavailable => GatewayError::Unavailable {
            code: "group_directory_unavailable".to_owned(),
        },
        GroupDirectoryError::InvalidRevision => GatewayError::Unavailable {
            code: "group_revision_invalid".to_owned(),
        },
    })?;
    // The session is frozen for, and later only reached through, the group
    // that was addressed. A directory that answers with another id (an alias
    // or a canonical form) would create a session its own URL cannot reach.
    if &frozen.group != group {
        return Err(GatewayError::Unavailable {
            code: "group_directory_mismatch".to_owned(),
        });
    }
    let shard = sid
        .0
        .split_once('.')
        .and_then(|(head, _)| head.strip_prefix('s'))
        .and_then(|digits| digits.parse::<u32>().ok())
        .ok_or_else(|| invalid("session_id_format"))?;
    let digest = content_digest(&json!({
        "group": frozen.group,
        "workflow": frozen.workflow,
        "participants": frozen.participants,
        "limits": frozen.limits,
    }));
    Ok(SessionSpec {
        schema_version: SCHEMA_VERSION,
        sid: sid.clone(),
        shard: ShardId(shard),
        scope: scope.clone(),
        kind: SessionKind::FsmGroup,
        group: frozen.group,
        workflow: frozen.workflow,
        participants: frozen.participants,
        limits: frozen.limits,
        digest,
        created_at: gateway.clock.now(),
    })
}

/// A user message that starts a task, in a new (`spec` set) or existing session.
fn new_task(
    gateway: &DistGateway,
    scope: &TaskScope,
    request: &SubmitInput,
    sid: Sid,
    spec: Option<SessionSpec>,
) -> AdmitTaskCommand {
    let tid = task_id(&sid, &request.message_id);
    let now = gateway.clock.now();
    let snapshot = TaskSnapshot {
        schema_version: SCHEMA_VERSION,
        scope: scope.clone(),
        sid: sid.clone(),
        tid: tid.clone(),
        content: TaskContent {
            status: TaskStatus::Submitted,
            interactions: Vec::new(),
            snapshot: Payload::Inline(serde_json::Value::Null),
            result_ref: None,
        },
        snapshot_event_id: None,
        source_commit: CommitSeq(0),
        created_at: now,
        updated_at: now,
    };
    AdmitTaskCommand {
        input: InputEnvelope {
            schema_version: SCHEMA_VERSION,
            input_id: user_input_id(scope, &request.group, &sid, &request.message_id),
            business_digest: content_digest(&json!({
                "kind": "user",
                "group": request.group.0,
                "content": request.content,
            })),
            sid,
            tid,
            body: InputBody::User {
                payload: request.content.clone(),
            },
        },
        create_session: spec,
        create_task: Some(snapshot),
    }
}

/// A message to an existing task: the answer to its open question. Without an
/// interaction in the request, the task's single open interaction is answered.
async fn reply(
    gateway: &DistGateway,
    scope: &TaskScope,
    request: &SubmitInput,
    tid: &Tid,
) -> Result<AdmitTaskCommand, GatewayError> {
    let sid = session_of_task(tid)?;
    if request.session.as_ref().is_some_and(|given| given != &sid) {
        return Err(invalid("task_not_in_session"));
    }
    gateway.check_group(scope, &sid, &request.group).await?;
    let interaction = match &request.interaction {
        Some(interaction) => interaction.clone(),
        None => {
            let cut = gateway.core.restore_task(scope, tid).await.map_err(core_error)?;
            let mut open = cut.view.content.interactions.into_iter();
            match (open.next(), open.next()) {
                (Some(only), None) => only.id,
                (None, _) => return Err(invalid("interaction_not_open")),
                (Some(_), Some(_)) => return Err(invalid("interaction_ambiguous")),
            }
        }
    };
    Ok(AdmitTaskCommand {
        input: InputEnvelope {
            schema_version: SCHEMA_VERSION,
            input_id: reply_input_id(scope, tid, &sid, &request.message_id),
            business_digest: content_digest(&json!({
                "kind": "human_reply",
                "tid": tid.0,
                "interaction": interaction.0,
                "content": request.content,
            })),
            sid,
            tid: tid.clone(),
            body: InputBody::HumanReply {
                interaction,
                payload: request.content.clone(),
            },
        },
        create_session: None,
        create_task: None,
    })
}
