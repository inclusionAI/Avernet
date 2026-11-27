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

/// In particular a retry after a lost first response has neither a task nor
/// a context id. Look up the scoped key before consulting today's directory.
/// Read-only replay never falls through into a write on a miss or an error.
pub(crate) async fn group_start(
    gateway: &DistGateway, caller: &GatewayCaller, request: SubmitInput,
) -> Result<AdmissionReceipt, GatewayError> {
    use bcs_service_api::core::dist_gateway::GroupStartLookup;
    use bcs_service_api::port::repo::distributed::group_round::GroupRoundOperation;

    let scope = scope_of(caller)?;
    check_request_id(&request.message_id)?;
    if request.group.0.is_empty() { return Err(invalid("group_missing")); }
    let grants = gateway.policy().grants(caller, &request.group).await;
    let can_start = grants.allows(GroupRoundOperation::New);
    if !can_start && !grants.read {
        return Err(GatewayError::AccessDenied);
    }
    let sid = match &request.session {
        Some(sid) => {
            parse_session(sid)?;
            check_source(sid, CreationSource::GroupA2a)?;
            gateway.check_group(&scope, sid, &request.group).await?;
            sid.clone()
        }
        None => group_session_id(&scope, &request.group, &request.message_id, gateway.config.shard_count),
    };
    let derived = Derivation::of(CreationSource::GroupA2a);
    let mut command = new_task(&scope, &request, sid.clone(), None, derived);
    match gateway.core.lookup_group_start(&scope, &request.group, &command.input.input_id)
        .await.map_err(core_error)? {
        GroupStartLookup::Accepted(receipt) => {
            if !grants.read { return Err(GatewayError::AccessDenied); }
            if receipt.business_digest != command.input.business_digest
                || receipt.input_id != command.input.input_id || receipt.sid != sid || receipt.tid != command.input.tid {
                return Err(GatewayError::Conflict);
            }
            if !can_start { return Ok(receipt); }
            // Keep the existing idempotent Worker repair path for an invoker:
            // the license CAS may have won before the Start/hint was sent.
            // Never submit a command on the read-only path. An accepted start
            // already has a frozen session spec; no directory resolve here.
            let CoreAdmission(repaired) = gateway.core.admit(&scope, command).await
                .map_err(|error| admit_error(error, &request.message_id))?;
            return Ok(repaired);
        }
        GroupStartLookup::Expired if grants.read => return Err(invalid("group_round_expired")),
        GroupStartLookup::Expired => return Err(GatewayError::AccessDenied),
        // A preparation isn't public. With read alone it is indistinguishable
        // from a miss; in particular it cannot be converted into a new start.
        GroupStartLookup::Prepared | GroupStartLookup::Absent if !can_start => return Err(GatewayError::AccessDenied),
        GroupStartLookup::Absent | GroupStartLookup::Prepared => {}
    }
    if request.session.is_none() {
        // A prepared bootstrap may already have a frozen spec. Do not replace
        // it with the current publication, even if the directory changed.
        match gateway.core.session_group(&scope, &sid).await {
            Ok(group) if group == request.group => {}
            Ok(_) => return Err(GatewayError::NotFound),
            Err(DistGatewayCoreError::NotFound) => {
                command.create_session = Some(new_spec(gateway, &scope, &request.group, &sid).await?);
            }
            Err(error) => return Err(core_error(error)),
        }
    }
    let CoreAdmission(receipt) = gateway.core.admit(&scope, command).await
        .map_err(|error| admit_error(error, &request.message_id))?;
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
            business_digest: if request.source == CreationSource::GroupA2a {
                content_digest(&json!({"kind": "group_start:v2", "context": request.session, "content": request.content}))
            } else {
                content_digest(&json!({"kind": "user", "group": request.group.0, "content": request.content}))
            },
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
