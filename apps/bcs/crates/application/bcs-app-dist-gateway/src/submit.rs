//! Admission use cases: a user message, a reply to an open question, a cancel.
//!
//! Every request names its entrance ([`CreationSource`]); identities are
//! derived in that source's namespace and a session presented from another
//! entrance stays invisible (plan T8 source isolation): the check reads the
//! creation source durably encoded in the session identity itself, never
//! caller or group equality.

use crate::error::{admit_error, core_error};
use crate::identity::*;
use crate::{CreationSource, Derivation, DistGateway, GatewayError, invalid, scope_of};
use bcs_dist_model::*;
use bcs_service_api::application::dist_gateway::*;
use bcs_service_api::core::dist_gateway::{AdmitTaskCommand, CoreAdmission, DistGatewayCoreError};
use bcs_service_api::port::dist_agent::{FrozenGroupDefinition, GroupDirectoryError};
use serde_json::json;

const SCHEMA_VERSION: u32 = 1;

/// The entrance that owns the session this request addresses: the session's
/// durable namespace must say the same, or the resource stays invisible.
fn check_source(sid: &Sid, source: CreationSource) -> Result<(), GatewayError> {
    if creation_source_of_sid(sid)? != source {
        return Err(GatewayError::NotFound);
    }
    Ok(())
}

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
    let derived = Derivation::of(request.source);
    let command = match (&request.session, &request.task) {
        (_, Some(tid)) => reply(gateway, &scope, &request, tid, derived).await?,
        (Some(sid), None) => {
            parse_session(sid)?;
            check_source(sid, request.source)?;
            gateway.check_group(&scope, sid, &request.group).await?;
            new_task(&scope, &request, sid.clone(), None, derived)
        }
        (None, None) => {
            let sid = (derived.session)(&scope, &request.group, &request.message_id, gateway.config.shard_count);
            let spec = match new_spec(gateway, &scope, &request.group, &sid).await {
                Ok(spec) => Some(spec),
                // A retry of a first message whose session is already frozen
                // needs no directory: when the directory cannot answer now,
                // the stored session is used and the admission is completed
                // or answered from its record.
                Err(error) => match gateway.core.session_group(&scope, &sid).await {
                    Ok(frozen) if frozen == request.group => None,
                    _ => return Err(error),
                },
            };
            new_task(&scope, &request, sid, spec, derived)
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
    let derived = Derivation::of(request.source);
    let sid = session_of_task(&request.tid)?;
    check_source(&sid, request.source)?;
    gateway.check_group(&scope, &sid, &request.group).await?;
    let body = InputBody::Cancel {
        reason: request.reason.clone(),
    };
    let command = AdmitTaskCommand {
        input: InputEnvelope {
            schema_version: SCHEMA_VERSION,
            input_id: (derived.cancel_input)(&scope, &request.tid, &sid, &request.request_id),
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
        start_task: false,
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
) -> Result<FrozenGroupDefinition, GatewayError> {
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
    let _ = sid;
    Ok(frozen)
}

/// A user message that starts a task, in a new (`spec` set) or existing session.
fn new_task(
    scope: &TaskScope,
    request: &SubmitInput,
    sid: Sid,
    spec: Option<FrozenGroupDefinition>,
    derived: Derivation,
) -> AdmitTaskCommand {
    let tid = (derived.task)(&sid, &request.message_id);
    AdmitTaskCommand {
        input: InputEnvelope {
            schema_version: SCHEMA_VERSION,
            input_id: (derived.user_input)(scope, &request.group, &sid, &request.message_id),
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
        start_task: true,
    }
}

/// A message to an existing task: the answer to its open question. Without an
/// interaction in the request, the task's single open interaction is answered.
async fn reply(
    gateway: &DistGateway,
    scope: &TaskScope,
    request: &SubmitInput,
    tid: &Tid,
    derived: Derivation,
) -> Result<AdmitTaskCommand, GatewayError> {
    let sid = session_of_task(tid)?;
    check_source(&sid, request.source)?;
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
            input_id: (derived.reply_input)(scope, tid, &sid, &request.message_id),
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
        start_task: false,
    })
}
