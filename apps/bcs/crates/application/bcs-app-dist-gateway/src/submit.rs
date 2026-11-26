//! Admission use cases: a user message, a reply to an open question, a cancel.

use crate::error::{admit_error, core_error};
use crate::identity::*;
use crate::{DistGateway, authenticated_scope, invalid, scope_of};
use bcs_dist_model::*;
use bcs_service_api::application::dist_gateway::*;
use bcs_service_api::core::dist_gateway::{AdmitTaskCommand, CoreAdmission, DistGatewayCoreError};
use bcs_service_api::port::dist_agent::{DirectoryError, FrozenSessionProposal};
use serde_json::json;

const SCHEMA_VERSION: u32 = 1;

pub(crate) async fn submit(
    gateway: &DistGateway,
    caller: &GatewayCaller,
    request: SubmitInput,
) -> Result<AdmissionReceipt, GatewayError> {
    let scope = scope_of(caller)?;
    check_request_id(&request.message_id)?;
    if request.target.id().is_empty() {
        return Err(invalid("target_missing"));
    }
    let actor = authenticated_scope(caller)?;
    // Delegated Direct chat authority uses the verified staff number, while
    // task ownership and interaction replies use the stable actor ID.
    let mut access_scope = actor.clone();
    if caller.access.acting_agent.is_some()
        && let bcs_service_api::application::principal::CallerContext::Human(human) = &caller.caller
    {
        if human.staff_no.is_empty() { return Err(GatewayError::AccessDenied); }
        access_scope.principal = PrincipalId(format!("human:{}", human.staff_no));
    }
    gateway.directory.authorize(&access_scope, &request.target, &caller.access).await.map_err(directory_error)?;
    let command = match (&request.session, &request.task) {
        (_, Some(tid)) => reply(gateway, &scope, &request, tid, actor.principal).await?,
        (Some(sid), None) => {
            parse_session(sid)?;
            gateway.check_target(&scope, sid, &request.target).await?;
            new_task(&scope, &request, sid.clone(), None)
        }
        (None, None) => {
            let sid = session_id(&scope, &request.target, &request.message_id, gateway.config.shard_count);
            let spec = match new_spec(gateway, &scope, &request.target).await {
                Ok(spec) => Some(spec),
                // An authorized retry can reuse the already-frozen configuration.
                Err(error) => match gateway.core.session_target(&scope, &sid).await {
                    Ok(frozen) if frozen == request.target => None,
                    _ => return Err(error),
                },
            };
            new_task(&scope, &request, sid, spec)
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
    gateway.check_target(&scope, &sid, &request.target).await?;
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
        start_task: false,
    };
    match gateway.core.admit(&scope, command).await {
        Ok(CoreAdmission(receipt)) => Ok(receipt),
        // The only state a cancel is refused in: the task is finished.
        Err(DistGatewayCoreError::InvalidTransition) => Err(GatewayError::NotCancelable),
        Err(error) => Err(admit_error(error, &request.request_id)),
    }
}

/// Freezes the target for a new session.
async fn new_spec(
    gateway: &DistGateway,
    scope: &TaskScope,
    target: &SessionTarget,
) -> Result<FrozenSessionProposal, GatewayError> {
    let frozen = gateway.directory.resolve(scope, target).await.map_err(directory_error)?;
    // The session is frozen for, and later only reached through, the target
    // that was addressed. A directory that answers with another id (an alias
    // or a canonical form) would create a session its own URL cannot reach.
    if &frozen.target != target {
        return Err(GatewayError::Unavailable {
            code: "session_directory_mismatch".to_owned(),
        });
    }
    Ok(frozen)
}

fn directory_error(error: DirectoryError) -> GatewayError {
    match error {
        DirectoryError::NotFound => GatewayError::NotFound,
        DirectoryError::AccessDenied => GatewayError::AccessDenied,
        DirectoryError::Unavailable => GatewayError::Unavailable { code: "session_directory_unavailable".into() },
        DirectoryError::InvalidRevision => GatewayError::Unavailable { code: "session_revision_invalid".into() },
    }
}

/// A user message that starts a task in a new or existing session.
fn new_task(
    scope: &TaskScope,
    request: &SubmitInput,
    sid: Sid,
    spec: Option<FrozenSessionProposal>,
) -> AdmitTaskCommand {
    let tid = task_id(&sid, &request.message_id);
    AdmitTaskCommand {
        input: InputEnvelope {
            schema_version: SCHEMA_VERSION,
            input_id: user_input_id(scope, &request.target, &sid, &request.message_id),
            business_digest: content_digest(&json!({
                "kind": "user",
                "target": request.target,
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
    responder: PrincipalId,
) -> Result<AdmitTaskCommand, GatewayError> {
    let sid = session_of_task(tid)?;
    if request.session.as_ref().is_some_and(|given| given != &sid) {
        return Err(invalid("task_not_in_session"));
    }
    gateway.check_target(scope, &sid, &request.target).await?;
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
                responder,
                payload: request.content.clone(),
            },
        },
        create_session: None,
        start_task: false,
    })
}
