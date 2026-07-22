use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use bcs_domain::{
    CollaborationDefinition, CollaborationDefinitionRef, CollaborationRuntimeDefinition,
    RuntimeParticipantBinding, StateMachineAssignee,
};
use bcs_protocol::CreateGroupRequest;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::{
    BotDetailCommand, BotGroupListCommand, CallbackChannelConfig,
    CollaborationRuntimeError, ConfigureGroupRuntimeCommand,
    DefaultDelivery, DmCreateCommand, GroupAddMemberCommand, GroupCreateCommand,
    GroupCreateParticipantCommand, GroupDeleteCommand, GroupDetailCommand, GroupDetailResult,
    GroupKind, GroupListCommand, GroupListEntry, GroupParticipantModeCommand,
    GroupPatchSettingsCommand, GroupRemoveMemberCommand,
    GroupRoutingPolicyCommand, GroupStatus,
    GroupStatusCommand, GroupTerminateCommand, GroupUpdateLabelCommand,
    GroupUpdateVisibilityCommand, GroupUpdateWorkspaceCommand, GroupUseCaseError,
    GroupWorkspaceQueryCommand, ParticipantMode, RoutingMode, RoutingPolicy,
    MAX_COLLABORATION_DEFINITION_YAML_BYTES, PatchGroupCollaborationDefinitionCommand,
    ServiceError, ServiceSpec, SessionKind, SessionStatus, StartStateMachineRunCommand,
    UpgradeGroupCollaborationDefinitionCommand, Workspace,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{BTreeMap, HashMap, HashSet};

use crate::error::HttpAdapterError;
use crate::state::HttpAppState;

use super::{
    bots::bot_use_case_error_to_http,
    authenticated_bot_from_headers, reject_judge_definition_when_unavailable,
    reject_judge_yaml_when_unavailable, validate_container_header,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum GroupKindFilter {
    #[default]
    Normal,
    Dm,
    All,
}

#[derive(Debug, Deserialize)]
pub struct ListGroupsQuery {
    #[serde(default)]
    pub offset: Option<u64>,
    #[serde(default)]
    pub limit: Option<u64>,
    #[serde(default)]
    pub group_kind: GroupKindFilter,
    #[serde(default)]
    pub visibility: Option<String>,
    #[serde(default)]
    pub label: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct ListBotGroupsQuery {
    #[serde(default)]
    pub offset: Option<u64>,
    #[serde(default)]
    pub limit: Option<u64>,
    #[serde(default)]
    pub group_kind: GroupKindFilter,
    #[serde(default)]
    pub q: Option<String>,
    /// Include groups where the actor participates only through a session.
    /// Defaults to true for backward compatibility.
    #[serde(default = "default_include_session_groups")]
    pub include_session_groups: bool,
}

fn default_include_session_groups() -> bool {
    true
}

#[derive(Debug, Deserialize)]
pub struct DeleteSessionQuery {
    pub bot_id: String,
}

#[derive(Debug, Deserialize)]
pub struct AddMemberRequest {
    pub bot_uuid: String,
    #[serde(default = "default_member_role")]
    pub role: String,
}

#[derive(Debug, Deserialize)]
pub struct UpdateGroupStatusRequest {
    pub status: String,
    #[serde(default)]
    pub reason: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct UpdateLabelRequest {
    #[serde(default)]
    pub label: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct UpdateRoutingPolicyRequest {
    #[serde(default)]
    pub mode: Option<RoutingMode>,
    #[serde(default)]
    pub default_bot_final_delivery: Option<DefaultDelivery>,
    #[serde(default)]
    pub sender_routes: Option<HashMap<String, Vec<String>>>,
}

#[derive(Debug, Deserialize)]
pub struct PutParticipantModeRequest {
    pub mode: ParticipantMode,
}

#[derive(Debug, Deserialize)]
pub struct PatchGroupCollaborationDefinitionRequest {
    pub base_definition: CollaborationDefinitionRef,
    pub definition_yaml: String,
    #[serde(default)]
    pub participant_bindings: Option<BTreeMap<String, RuntimeParticipantBinding>>,
}

#[derive(Debug, Deserialize)]
pub struct UpgradeGroupCollaborationDefinitionRequest {
    pub base_definition: CollaborationDefinitionRef,
    pub target_definition: CollaborationDefinitionRef,
    #[serde(default)]
    pub participant_bindings: Option<BTreeMap<String, RuntimeParticipantBinding>>,
}

pub async fn create_group(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<CreateGroupRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    let caller_actor_id = resolve_group_create_caller(&state, &headers, &uri).await?;
    let collaboration_definition_yaml = req.collaboration_definition_yaml.clone();
    let auto_start_on_service_invocation =
        req.auto_start_on_service_invocation.unwrap_or(false);
    let group_kind = req
        .group_kind
        .as_deref()
        .map(group_kind_from_str)
        .transpose()?;
    if collaboration_definition_yaml.is_some() && group_kind == Some(GroupKind::Dm) {
        return Err(HttpAdapterError::BadRequest(
            "collaboration_definition_yaml is only supported for normal groups".to_string(),
        ));
    }
    if collaboration_definition_yaml.is_some() {
        if let Some(strategy) = req.group_strategy.as_deref() {
            if strategy != "state_machine" {
                return Err(HttpAdapterError::BadRequest(
                    "collaboration_definition_yaml requires group_strategy=state_machine"
                        .to_string(),
                ));
            }
        }
    }
    if !req.participant_bindings.is_empty() && collaboration_definition_yaml.is_none() {
        return Err(HttpAdapterError::BadRequest(
            "participant_bindings requires collaboration_definition_yaml".to_string(),
        ));
    }
    let collaboration_definition =
        if let Some(definition_yaml) = collaboration_definition_yaml.as_deref() {
            let definition = parse_authoring_collaboration_definition_yaml(definition_yaml)?;
            reject_judge_definition_when_unavailable(&state, &definition)?;
            Some(definition)
        } else {
            None
        };
    if group_kind == Some(GroupKind::Dm) {
        let target_actor_id = dm_target_actor_id(&req)?.to_string();

        let result = state
            .services
            .group_management
            .create_dm(DmCreateCommand {
                group_id: req.id,
                caller_actor_id,
                driver_bot: req.driver_bot.clone(),
                target_actor_id,
                label: req.label,
                topic: req.topic,
                context: req.context,
            })
            .await
            .map_err(group_use_case_error_to_http)?;
        let mut detail = result.group;
        detail.chat_url = state.botchat_url.as_ref().map(|base| {
            build_group_chat_url(
                base,
                &detail.group_id,
                &detail.driver_bot_id,
                detail.latest_running_session_id.as_deref(),
            )
        });

        return Ok(Json(group_detail_to_create_json(detail, result.created)));
    }

    let driver_bot = req.driver_bot.clone().ok_or_else(|| {
        HttpAdapterError::BadRequest("driver_bot is required for normal group creation".to_string())
    })?;
    let originator = req.originator.clone().unwrap_or_else(|| driver_bot.clone());
    let state_machine_group = collaboration_definition_yaml.is_some()
        || req.group_strategy.as_deref() == Some("state_machine");
    let participants = group_create_participants(
        &req,
        collaboration_definition.as_ref(),
        &driver_bot,
        state_machine_group,
    )?;
    let member_bot_ids = participants
        .iter()
        .map(|participant| participant.bot_id.clone())
        .collect::<Vec<_>>();
    let group_strategy = if collaboration_definition_yaml.is_some() {
        Some(bcs_service_api::GroupStrategy::StateMachine)
    } else {
        req.group_strategy.as_deref().map(|s| match s {
            "manager_worker" => bcs_service_api::GroupStrategy::ManagerWorker,
            "state_machine" => bcs_service_api::GroupStrategy::StateMachine,
            _ => bcs_service_api::GroupStrategy::Chat,
        })
    };
    let participant_bindings = runtime_participant_bindings_from_request(&req);
    validate_state_machine_runtime_bindings_before_create(
        collaboration_definition.as_ref(),
        &participants,
        &req.participant_bindings,
    )?;
    let collaboration_definition_ref =
        if let (Some(definition), Some(source_yaml)) = (
            collaboration_definition.as_ref(),
            collaboration_definition_yaml.clone(),
        ) {
            let definition_ref = CollaborationDefinitionRef {
                id: definition.id.clone(),
                version: definition.version,
            };
            state
                .services
                .collaboration_runtime
                .upsert_definition_with_source_yaml(definition.clone(), source_yaml)
                .await
                .map_err(collaboration_runtime_error_to_http)?;
            Some(definition_ref)
        } else {
            None
        };
    let cmd = GroupCreateCommand {
        group_id: req.id,
        caller_actor_id: caller_actor_id.clone(),
        driver_bot_id: driver_bot,
        originator: Some(originator),
        label: req.label,
        topic: req.topic,
        context: req.context,
        routing_policy: routing_policy_from_protocol_value(req.routing_policy)?,
        participants,
        member_bot_ids,
        group_kind,
        service_spec: req.service_spec.clone().and_then(|v| serde_json::from_value(v).ok()),
        group_strategy,
        visibility: req.visibility,
    };
    let mut result = state
        .services
        .group_management
        .create_group(cmd)
        .await
        .map_err(group_use_case_error_to_http)?;

    if let Some(definition_ref) = collaboration_definition_ref {
        state
            .services
            .collaboration_runtime
            .configure_group_runtime(ConfigureGroupRuntimeCommand {
                group_id: result.group_id.clone(),
                definition_yaml: None,
                definition: None,
                definition_ref: Some(definition_ref),
                participant_bindings,
                auto_start_on_service_invocation,
            })
            .await
            .map_err(collaboration_runtime_error_to_http)?;
        if result.group_strategy == bcs_service_api::GroupStrategy::StateMachine {
            if let Some(run_id) = start_initial_state_machine_run_for_group(
                &state,
                &result,
                caller_actor_id.clone(),
            )
            .await?
            {
                tracing::info!(
                    group_id = %result.group_id,
                    run_id = %run_id,
                    "started default state-machine run for new group"
                );
            }
        }
    }

    result.chat_url = state.botchat_url.as_ref().map(|base| {
        build_group_chat_url(
            base,
            &result.group_id,
            &result.driver_bot_id,
            result.latest_running_session_id.as_deref(),
        )
    });

    Ok(Json(group_detail_to_create_json(result, true)))
}

async fn start_initial_state_machine_run_for_group(
    state: &HttpAppState,
    group: &GroupDetailResult,
    caller_id: Option<String>,
) -> Result<Option<String>, HttpAdapterError> {
    let Some(session_id) = group.latest_running_session_id.as_deref() else {
        return Ok(None);
    };
    let session = state
        .services
        .session_management
        .get(session_id)
        .await
        .map_err(|error| {
            HttpAdapterError::Service(ServiceError::InternalError(error.to_string()))
        })?;
    let Some(session) = session else {
        tracing::warn!(
            group_id = %group.group_id,
            session_id = %session_id,
            "default state-machine session not found after group creation"
        );
        return Ok(None);
    };
    if session.session_kind != SessionKind::ServiceInvocation {
        return Ok(None);
    }
    let run = state
        .services
        .collaboration_runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: group.group_id.clone(),
            session_id: Some(session.id.clone()),
            definition_yaml: None,
            definition: None,
            definition_ref: None,
            input: session.input.clone().unwrap_or(Value::Null),
            caller_id,
        })
        .await
        .map_err(collaboration_runtime_error_to_http)?;
    Ok(Some(run.view.run.run_id))
}

pub async fn list_groups(
    State(state): State<HttpAppState>,
    Query(query): Query<ListGroupsQuery>,
) -> Result<Json<Value>, HttpAdapterError> {
    let offset = query.offset.unwrap_or(0);
    let limit = query.limit.unwrap_or(10);
    let kind_filter = group_kind_filter(query.group_kind);
    let result = state
        .services
        .group_query
        .list_groups(GroupListCommand {
            group_kind: kind_filter,
            offset,
            limit,
            visibility: query.visibility.clone(),
            label: query.label.clone(),
        })
        .await
        .map_err(group_use_case_error_to_http)?;
    let items: Vec<Value> = result
        .items
        .into_iter()
        .map(group_list_entry_to_legacy_json)
        .collect();

    Ok(Json(serde_json::json!({
        "items": items,
        "total": result.total,
        "offset": result.offset,
        "limit": result.limit,
        "group_kind": query.group_kind,
    })))
}

pub async fn get_group(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, HttpAdapterError> {
    let mut group = state
        .services
        .group_query
        .get_group(GroupDetailCommand { group_id: id })
        .await
        .map_err(group_use_case_error_to_http)?;

    // Only expose latest_running_session_id for non-service groups
    if group.service_spec.is_none() {
        if let Ok(Some(session)) = state.services.session_management
            .list_by_group(&group.group_id, Some(SessionStatus::Running), 0, 1, None, None)
            .await
            .map(|v| v.into_iter().next())
        {
            group.latest_running_session_id = Some(session.id);
        }
    }

    let mut json = group_to_detail_json(group.clone());
    // Resolve driver bot owner info
    let (driver_bot_owner, driver_bot_owner_name) =
        resolve_driver_bot_owner(&state, &group.driver_bot_id).await;
    if let Some(obj) = json.as_object_mut() {
        obj.insert("driver_bot_owner".to_string(), serde_json::json!(driver_bot_owner));
        obj.insert("driver_bot_owner_name".to_string(), serde_json::json!(driver_bot_owner_name));
    }

    Ok(Json(json))
}

pub async fn get_group_collaboration_definition(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, HttpAdapterError> {
    let view = state
        .services
        .collaboration_runtime
        .get_group_collaboration_definition(&id)
        .await
        .map_err(collaboration_runtime_error_to_http)?;
    serde_json::to_value(view)
        .map(Json)
        .map_err(|error| HttpAdapterError::Service(ServiceError::InternalError(error.to_string())))
}

pub async fn patch_group_collaboration_definition(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    Json(req): Json<PatchGroupCollaborationDefinitionRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    reject_judge_yaml_when_unavailable(&state, &req.definition_yaml, "definition_yaml")?;
    let view = state
        .services
        .collaboration_runtime
        .patch_group_collaboration_definition(PatchGroupCollaborationDefinitionCommand {
            group_id: id,
            base_definition: req.base_definition,
            definition_yaml: req.definition_yaml,
            participant_bindings: req.participant_bindings,
        })
        .await
        .map_err(collaboration_runtime_error_to_http)?;
    serde_json::to_value(view)
        .map(Json)
        .map_err(|error| HttpAdapterError::Service(ServiceError::InternalError(error.to_string())))
}

pub async fn upgrade_group_collaboration_definition(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    Json(req): Json<UpgradeGroupCollaborationDefinitionRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    let view = state
        .services
        .collaboration_runtime
        .upgrade_group_collaboration_definition(UpgradeGroupCollaborationDefinitionCommand {
            group_id: id,
            base_definition: req.base_definition,
            target_definition: req.target_definition,
            participant_bindings: req.participant_bindings,
        })
        .await
        .map_err(collaboration_runtime_error_to_http)?;
    serde_json::to_value(view)
        .map(Json)
        .map_err(|error| HttpAdapterError::Service(ServiceError::InternalError(error.to_string())))
}

pub async fn delete_group(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    Query(query): Query<DeleteSessionQuery>,
) -> Result<Json<Value>, HttpAdapterError> {
    let result = state
        .services
        .group_management
        .delete_group(GroupDeleteCommand {
            caller_actor_id: query.bot_id,
            group_id: id.clone(),
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "deleted": result.deleted,
        "id": result.group_id
    })))
}

pub async fn list_bot_groups(
    State(state): State<HttpAppState>,
    Path(bot_uuid): Path<String>,
    Query(query): Query<ListBotGroupsQuery>,
) -> Result<Json<Value>, HttpAdapterError> {
    let offset = query.offset.unwrap_or(0);
    let limit = query.limit.unwrap_or(10);
    let kind_filter = group_kind_filter(query.group_kind);

    let result = state
        .services
        .group_query
        .list_bot_groups(BotGroupListCommand {
            bot_id: bot_uuid.clone(),
            group_kind: kind_filter,
            q: query.q.clone(),
            offset,
            limit,
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    if !query.include_session_groups {
        let items: Vec<Value> = result
            .items
            .into_iter()
            .map(bot_group_list_entry_to_legacy_json)
            .collect();

        return Ok(Json(serde_json::json!({
            "bot_uuid": bot_uuid,
            "items": items,
            "total": result.total,
            "offset": result.offset,
            "limit": result.limit,
        })));
    }

    // Union: include groups where the actor is only a session participant
    // (not in group.participants), so humans added to a session via PATCH
    // can still see the group in their listing.
    let existing_ids: HashSet<String> = result
        .items
        .iter()
        .map(|e| e.group_id.clone())
        .collect();
    let mut session_extra: Vec<GroupListEntry> = Vec::new();
    if let Ok(session_group_ids) = state
        .services
        .session_management
        .list_group_ids_by_session_participant(&bot_uuid)
        .await
    {
        for gid in session_group_ids {
            if existing_ids.contains(&gid) {
                continue;
            }
            let group = match state
                .services
                .group_query
                .get_group(GroupDetailCommand { group_id: gid.clone() })
                .await
            {
                Ok(group) => group,
                Err(_) => continue,
            };
            // Apply kind filter
            if let Some(kind) = kind_filter {
                if group.group_kind != kind {
                    continue;
                }
            }
            if !group_detail_matches_bot_group_query(&group, query.q.as_deref()) {
                continue;
            }
            let participant_count = group.participants.len();
            let message_count = group.message_count;
            session_extra.push(GroupListEntry {
                group_id: group.group_id,
                label: group.label,
                driver_bot_id: group.driver_bot_id,
                originator: group.originator,
                context: group.context,
                participants: group.participants,
                participant_count,
                message_count,
                created_at: group.created_at,
                updated_at: group.updated_at,
                group_kind: group.group_kind,
                group_strategy: group.group_strategy,
                visibility: group.visibility,
            });
        }
    }

    let mut all_items: Vec<GroupListEntry> = result.items;
    if session_extra.is_empty() {
        let items: Vec<Value> = all_items
            .into_iter()
            .map(bot_group_list_entry_to_legacy_json)
            .collect();

        return Ok(Json(serde_json::json!({
            "bot_uuid": bot_uuid,
            "items": items,
            "total": result.total,
            "offset": result.offset,
            "limit": result.limit,
        })));
    }

    let session_extra_len = session_extra.len() as u64;
    all_items.extend(session_extra);

    // Re-apply pagination (service layer already paginated formal results;
    // session-only extras are typically few, so we re-paginate the merged set).
    all_items.sort_by(|a, b| b.updated_at.cmp(&a.updated_at));
    let total = result.total.saturating_add(session_extra_len);
    let items: Vec<Value> = all_items
        .into_iter()
        .skip(offset as usize)
        .take(limit as usize)
        .map(bot_group_list_entry_to_legacy_json)
        .collect();

    Ok(Json(serde_json::json!({
        "bot_uuid": bot_uuid,
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
    })))
}

fn group_detail_matches_bot_group_query(group: &GroupDetailResult, q: Option<&str>) -> bool {
    let Some(q) = q.map(str::trim).filter(|q| !q.is_empty()) else {
        return true;
    };
    let q = q.to_lowercase();
    group
        .label
        .as_deref()
        .unwrap_or("")
        .to_lowercase()
        .contains(&q)
}

pub async fn add_group_member(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<AddMemberRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    let requester_bot_id = resolve_group_member_caller(&state, &headers, &uri, &id).await?;
    let AddMemberRequest { bot_uuid, role } = req;
    let response_role = role.clone();
    let result = state
        .services
        .group_management
        .add_member(GroupAddMemberCommand {
            caller_actor_id: Some(requester_bot_id),
            human_actor_id: extract_human_actor_id(&state, &headers, &uri).await,
            group_id: id.clone(),
            bot_id: bot_uuid,
            role: Some(role),
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "added": true,
        "session_id": result.group_id,
        "member": {
            "bot_uuid": result.member.bot_uuid,
            "role": response_role,
        }
    })))
}

pub async fn remove_group_member(
    State(state): State<HttpAppState>,
    Path((id, bot_uuid)): Path<(String, String)>,
    headers: HeaderMap,
    uri: Uri,
) -> Result<Json<Value>, HttpAdapterError> {
    let requester_bot_id = resolve_actor_caller(&state, &headers, &uri).await?;
    let result = state
        .services
        .group_management
        .remove_member(GroupRemoveMemberCommand {
            caller_actor_id: Some(requester_bot_id),
            group_id: id,
            bot_id: bot_uuid,
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "removed": true,
        "group_id": result.group_id,
        "removed_bot_uuid": result.removed_bot_uuid,
    })))
}

pub async fn update_group_status(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(req): Json<UpdateGroupStatusRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    let requester_bot_id = authenticated_bot_from_headers(&state, &headers).await?;
    let status = req.status.clone();
    let result = state
        .services
        .group_management
        .update_status(GroupStatusCommand {
            caller_actor_id: Some(requester_bot_id.clone()),
            group_id: id.clone(),
            status,
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "updated": true,
        "group_id": id,
        "status": group_status_to_wire(result.status),
        "reason": req.reason,
        "changed_by": requester_bot_id,
    })))
}

pub async fn terminate_group(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, HttpAdapterError> {
    let caller_bot_id = authenticated_bot_from_headers(&state, &headers).await?;
    let session = state
        .services
        .group_management
        .terminate_group(GroupTerminateCommand {
            caller_actor_id: caller_bot_id.clone(),
            group_id: id.clone(),
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "terminated": true,
        "group_id": id,
        "status": "completed",
        "terminated_by": caller_bot_id,
        "session": {
            "id": session.group_id,
            "label": session.label,
            "driver_bot": session.driver_bot_id,
            "participants": session.participants.iter().map(|p| &p.bot_uuid).collect::<Vec<_>>(),
            "status": group_status_to_wire(session.status),
        }
    })))
}

pub async fn update_group_label(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<UpdateLabelRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    let requester_bot_id = resolve_group_member_caller(&state, &headers, &uri, &id).await?;
    let result = state
        .services
        .group_management
        .update_label(GroupUpdateLabelCommand {
            caller_actor_id: requester_bot_id.clone(),
            group_id: id.clone(),
            label: req.label.clone(),
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "updated": true,
        "group_id": id,
        "label": result.label,
        "changed_by": requester_bot_id,
    })))
}

#[derive(Debug, Deserialize)]
pub struct UpdateVisibilityRequest {
    pub visibility: String,
}

pub async fn update_group_visibility(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<UpdateVisibilityRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    let requester_bot_id = resolve_group_member_caller(&state, &headers, &uri, &id).await?;
    let result = state
        .services
        .group_management
        .update_visibility(GroupUpdateVisibilityCommand {
            caller_actor_id: requester_bot_id.clone(),
            group_id: id.clone(),
            visibility: req.visibility.clone(),
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "updated": true,
        "group_id": id,
        "visibility": result.visibility,
        "changed_by": requester_bot_id,
    })))
}

pub async fn get_workspace(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, HttpAdapterError> {
    let result = state
        .services
        .group_query
        .get_workspace(GroupWorkspaceQueryCommand { group_id: id })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(
        serde_json::to_value(result.workspace).unwrap_or_default(),
    ))
}

pub async fn update_workspace(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    Json(workspace): Json<Workspace>,
) -> Result<Json<Value>, HttpAdapterError> {
    let result = state
        .services
        .group_management
        .update_workspace(GroupUpdateWorkspaceCommand {
            caller_actor_id: None,
            group_id: id.clone(),
            workspace,
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "updated": true,
        "group_id": result.group_id,
        "workspace": result.workspace,
    })))
}

pub async fn update_routing_policy(
    State(state): State<HttpAppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(req): Json<UpdateRoutingPolicyRequest>,
) -> Result<Json<Value>, HttpAdapterError> {
    let requester_bot_id = authenticated_bot_from_headers(&state, &headers).await?;
    let result = state
        .services
        .group_management
        .update_routing_policy(GroupRoutingPolicyCommand {
            caller_actor_id: Some(requester_bot_id),
            group_id: id,
            mode: req.mode,
            default_bot_final_delivery: req.default_bot_final_delivery,
            sender_routes: req.sender_routes,
        })
        .await
        .map_err(group_use_case_error_to_http)?;

    Ok(Json(serde_json::json!({
        "ok": true,
        "routing_policy": result.routing_policy,
    })))
}

pub async fn put_participant_mode(
    State(state): State<HttpAppState>,
    Path((group_id, actor_id)): Path<(String, String)>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<PutParticipantModeRequest>,
) -> Response {
    let caller = match resolve_put_caller(&state, &headers, &uri).await {
        Ok(caller) => caller,
        Err(response) => return response,
    };

    let result = match state
        .services
        .group_management
        .update_participant_mode(GroupParticipantModeCommand {
            caller_actor_id: caller,
            group_id,
            actor_id,
            mode: req.mode,
        })
        .await
    {
        Ok(result) => result,
        Err(error) => return group_use_case_error_to_mode_response(error).into_response(),
    };

    (
        StatusCode::OK,
        Json(serde_json::json!({
            "success": true,
            "data": {
                "group_id": result.group_id,
                "actor_id": result.actor_id,
                "mode": result.mode,
            }
        })),
    )
        .into_response()
}

async fn resolve_group_create_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Result<Option<String>, HttpAdapterError> {
    if let Some(caller) = state.bot_uuid_from_headers(headers).await {
        validate_container_header(state, headers, &caller)?;
        return Ok(Some(caller));
    }

    Ok(state
        .user_identity
        .extract(headers, uri)
        .await
        .and_then(|identity| identity.staff_no)
        .filter(|staff_no| !staff_no.is_empty())
        .map(|staff_no| format!("human_{}", staff_no)))
}

fn dm_target_actor_id(req: &CreateGroupRequest) -> Result<&str, HttpAdapterError> {
    if let Some(target_actor_id) = req.target_actor_id.as_deref() {
        if target_actor_id.trim().is_empty() {
            return Err(HttpAdapterError::BadRequest(
                "target_actor_id must not be empty for DM group creation".to_string(),
            ));
        }
        if req.participants.len() > 1 {
            return Err(HttpAdapterError::BadRequest(
                "DM group creation accepts at most one participant target".to_string(),
            ));
        }
        if let Some(participant) = req.participants.first() {
            if participant.bot_uuid != target_actor_id {
                return Err(HttpAdapterError::BadRequest(
                    "participants[0].bot_uuid must match target_actor_id for DM group creation"
                        .to_string(),
                ));
            }
        }
        return Ok(target_actor_id);
    }

    match req.participants.as_slice() {
        [participant] if !participant.bot_uuid.trim().is_empty() => Ok(participant.bot_uuid.as_str()),
        [..] if req.participants.is_empty() => Err(HttpAdapterError::BadRequest(
            "target_actor_id is required for DM group creation".to_string(),
        )),
        [_] => Err(HttpAdapterError::BadRequest(
            "participants[0].bot_uuid must not be empty for DM group creation".to_string(),
        )),
        _ => Err(HttpAdapterError::BadRequest(
            "DM group creation requires exactly one participant target when target_actor_id is omitted"
                .to_string(),
        )),
    }
}

fn group_create_participants(
    req: &CreateGroupRequest,
    collaboration_definition: Option<&CollaborationDefinition>,
    driver_bot: &str,
    state_machine_group: bool,
) -> Result<Vec<GroupCreateParticipantCommand>, HttpAdapterError> {
    if !req.participants.is_empty() {
        if state_machine_group && req.participants.iter().any(|participant| participant.role.is_some()) {
            return Err(HttpAdapterError::BadRequest(
                "state-machine group participants.role is inferred by BCS and must not be provided"
                    .to_string(),
            ));
        }
        if req.participant_bindings.is_empty() {
            validate_definition_has_legacy_bot_ids(collaboration_definition)?;
        }
        validate_participant_binding_members(req, collaboration_definition)?;
        return Ok(req
            .participants
            .iter()
            .map(|participant| GroupCreateParticipantCommand {
                bot_id: participant.bot_uuid.clone(),
                role: if state_machine_group {
                    Some(inferred_participant_role_wire(&participant.bot_uuid, driver_bot).to_string())
                } else {
                    participant.role.clone()
                },
            })
            .collect());
    }

    if !req.participant_bindings.is_empty() {
        validate_participant_binding_members(req, collaboration_definition)?;
        return group_create_participants_from_runtime_bindings(req, driver_bot);
    }

    let Some(definition) = collaboration_definition else {
        return Ok(Vec::new());
    };

    let mut participants = Vec::new();
    let mut has_driver_bot = false;

    for (binding_id, binding) in &definition.participants {
        if binding.bcs_participant_role.is_some() {
            return Err(HttpAdapterError::BadRequest(format!(
                "collaboration_definition_yaml participant '{}' must not define bcs_participant_role",
                binding_id
            )));
        }
        let Some(bot_id) = binding
            .bot_id
            .as_deref()
            .map(str::trim)
            .filter(|bot_id| !bot_id.is_empty())
        else {
            return Err(HttpAdapterError::BadRequest(format!(
                "collaboration_definition_yaml participant '{}' must define bot_id",
                binding_id
            )));
        };

        if bot_id == driver_bot {
            has_driver_bot = true;
        }

        let role = inferred_participant_role_wire(bot_id, driver_bot).to_string();

        if let Some(index) = participants
            .iter()
            .position(|participant: &GroupCreateParticipantCommand| participant.bot_id == bot_id)
        {
            participants[index].role = Some(role);
            continue;
        }

        participants.push(GroupCreateParticipantCommand {
            bot_id: bot_id.to_string(),
            role: Some(role),
        });
    }

    if participants.is_empty() {
        return Err(HttpAdapterError::BadRequest(
            "collaboration_definition_yaml participants must contain at least one bot_id"
                .to_string(),
        ));
    }
    if !has_driver_bot {
        return Err(HttpAdapterError::BadRequest(
            "driver_bot must appear in collaboration_definition_yaml participants".to_string(),
        ));
    }

    Ok(participants)
}

fn group_create_participants_from_runtime_bindings(
    req: &CreateGroupRequest,
    driver_bot: &str,
) -> Result<Vec<GroupCreateParticipantCommand>, HttpAdapterError> {
    let mut participants = Vec::new();
    let mut seen = HashSet::new();
    let mut has_driver_bot = false;
    for (binding_id, binding) in &req.participant_bindings {
        validate_runtime_binding_wire(binding_id, binding)?;
        for raw_bot_id in &binding.bot_ids {
            let bot_id = raw_bot_id.trim();
            if bot_id == driver_bot {
                has_driver_bot = true;
            }
            if !seen.insert(bot_id.to_string()) {
                continue;
            }
            participants.push(GroupCreateParticipantCommand {
                bot_id: bot_id.to_string(),
                role: Some(inferred_participant_role_wire(bot_id, driver_bot).to_string()),
            });
        }
    }
    if participants.is_empty() {
        return Err(HttpAdapterError::BadRequest(
            "participant_bindings must resolve to at least one bot participant".to_string(),
        ));
    }
    if !has_driver_bot {
        return Err(HttpAdapterError::BadRequest(
            "driver_bot must appear in participant_bindings".to_string(),
        ));
    }
    Ok(participants)
}

fn validate_participant_binding_members(
    req: &CreateGroupRequest,
    collaboration_definition: Option<&CollaborationDefinition>,
) -> Result<(), HttpAdapterError> {
    if req.participant_bindings.is_empty() {
        return Ok(());
    }
    let Some(definition) = collaboration_definition else {
        return Err(HttpAdapterError::BadRequest(
            "participant_bindings requires collaboration_definition_yaml".to_string(),
        ));
    };
    for binding_id in req.participant_bindings.keys() {
        if !definition.participants.contains_key(binding_id) {
            return Err(HttpAdapterError::BadRequest(format!(
                "participant_bindings contains undeclared slot: {binding_id}"
            )));
        }
    }
    let explicit_participants = req
        .participants
        .iter()
        .map(|participant| participant.bot_uuid.as_str())
        .collect::<HashSet<_>>();
    for (binding_id, binding) in &req.participant_bindings {
        validate_runtime_binding_wire(binding_id, binding)?;
        if !explicit_participants.is_empty() {
            for bot_id in &binding.bot_ids {
                if !explicit_participants.contains(bot_id.trim()) {
                    return Err(HttpAdapterError::BadRequest(format!(
                        "participant_bindings.{binding_id} bot_id is not in participants: {}",
                        bot_id.trim()
                    )));
                }
            }
        }
    }
    Ok(())
}

fn validate_state_machine_runtime_bindings_before_create(
    collaboration_definition: Option<&CollaborationDefinition>,
    participants: &[GroupCreateParticipantCommand],
    participant_bindings: &BTreeMap<String, bcs_protocol::ParticipantBindingInfo>,
) -> Result<(), HttpAdapterError> {
    if participant_bindings.is_empty() {
        return Ok(());
    }
    let Some(definition) = collaboration_definition else {
        return Ok(());
    };
    let referenced_slots = referenced_participant_slots(definition)?;
    let group_participants = participants
        .iter()
        .map(|participant| participant.bot_id.as_str())
        .collect::<HashSet<_>>();
    let mut resolved_slots = HashSet::new();

    for (slot, definition_binding) in &definition.participants {
        let bot_ids = if let Some(runtime_binding) = participant_bindings.get(slot) {
            participant_binding_bot_ids(slot, runtime_binding)?
        } else if let Some(bot_id) = definition_binding
            .bot_id
            .as_deref()
            .map(str::trim)
            .filter(|bot_id| !bot_id.is_empty())
        {
            vec![bot_id.to_string()]
        } else if definition_binding.required || referenced_slots.contains(slot) {
            return Err(HttpAdapterError::BadRequest(format!(
                "participant slot {slot} has no runtime binding or legacy bot_id"
            )));
        } else {
            continue;
        };

        for bot_id in &bot_ids {
            if !group_participants.contains(bot_id.as_str()) {
                return Err(HttpAdapterError::BadRequest(format!(
                    "participant slot {slot} bot_id is not a group participant: {bot_id}"
                )));
            }
        }
        if referenced_slots.contains(slot) && bot_ids.len() != 1 {
            return Err(HttpAdapterError::BadRequest(format!(
                "participant slot {slot} is assigned to a node and must resolve to exactly one bot in the current runtime"
            )));
        }
        resolved_slots.insert(slot.clone());
    }

    for slot in &referenced_slots {
        if !resolved_slots.contains(slot) {
            return Err(HttpAdapterError::BadRequest(format!(
                "node assignee binding has no resolved participant slot: {slot}"
            )));
        }
    }
    Ok(())
}

fn parse_authoring_collaboration_definition_yaml(
    yaml: &str,
) -> Result<CollaborationDefinition, HttpAdapterError> {
    reject_authoring_yaml_identity(yaml)?;
    serde_yaml::from_str(yaml).map_err(|error| {
        HttpAdapterError::BadRequest(format!("invalid collaboration_definition_yaml: {error}"))
    })
}

fn reject_authoring_yaml_identity(yaml: &str) -> Result<(), HttpAdapterError> {
    if yaml.as_bytes().len() > MAX_COLLABORATION_DEFINITION_YAML_BYTES {
        return Err(HttpAdapterError::BadRequest(format!(
            "collaboration_definition_yaml exceeds {} bytes",
            MAX_COLLABORATION_DEFINITION_YAML_BYTES
        )));
    }
    let value: serde_yaml::Value = serde_yaml::from_str(yaml).map_err(|error| {
        HttpAdapterError::BadRequest(format!("invalid collaboration_definition_yaml: {error}"))
    })?;
    let Some(mapping) = value.as_mapping() else {
        return Ok(());
    };
    for key in mapping.keys() {
        if matches!(key.as_str(), Some("id" | "version")) {
            return Err(HttpAdapterError::BadRequest(
                "collaboration_definition_yaml must not contain top-level id or version"
                    .to_string(),
            ));
        }
    }
    Ok(())
}

fn referenced_participant_slots(
    definition: &CollaborationDefinition,
) -> Result<HashSet<String>, HttpAdapterError> {
    let state_machine = match &definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => {
            return Err(HttpAdapterError::BadRequest(
                "runtime.kind must be state_machine".to_string(),
            ))
        }
    };
    let mut slots = HashSet::new();
    for node in state_machine.nodes.values() {
        if let Some(StateMachineAssignee::BotBinding { binding }) = &node.assignee {
            slots.insert(binding.clone());
        }
    }
    Ok(slots)
}

fn validate_definition_has_legacy_bot_ids(
    collaboration_definition: Option<&CollaborationDefinition>,
) -> Result<(), HttpAdapterError> {
    let Some(definition) = collaboration_definition else {
        return Ok(());
    };
    for (binding_id, binding) in &definition.participants {
        if binding.bot_id.as_deref().map(str::trim).filter(|bot_id| !bot_id.is_empty()).is_none() {
            return Err(HttpAdapterError::BadRequest(format!(
                "collaboration_definition_yaml participant '{}' must define bot_id or be provided via participant_bindings",
                binding_id
            )));
        }
    }
    Ok(())
}

fn participant_binding_bot_ids(
    binding_id: &str,
    binding: &bcs_protocol::ParticipantBindingInfo,
) -> Result<Vec<String>, HttpAdapterError> {
    validate_runtime_binding_wire(binding_id, binding)?;
    Ok(binding
        .bot_ids
        .iter()
        .map(|bot_id| bot_id.trim().to_string())
        .collect())
}

fn validate_runtime_binding_wire(
    binding_id: &str,
    binding: &bcs_protocol::ParticipantBindingInfo,
) -> Result<(), HttpAdapterError> {
    if binding.source != "manual" {
        return Err(HttpAdapterError::BadRequest(format!(
            "participant_bindings.{binding_id}.source must be manual"
        )));
    }
    let mut seen = HashSet::new();
    for bot_id in &binding.bot_ids {
        let bot_id = bot_id.trim();
        if bot_id.is_empty() {
            return Err(HttpAdapterError::BadRequest(format!(
                "participant_bindings.{binding_id}.bot_ids must not contain empty bot_id"
            )));
        }
        if !seen.insert(bot_id.to_string()) {
            return Err(HttpAdapterError::BadRequest(format!(
                "participant_bindings.{binding_id}.bot_ids contains duplicate bot_id: {bot_id}"
            )));
        }
    }
    if binding.bot_ids.is_empty() {
        return Err(HttpAdapterError::BadRequest(format!(
            "participant_bindings.{binding_id}.bot_ids must not be empty"
        )));
    }
    Ok(())
}

fn runtime_participant_bindings_from_request(
    req: &CreateGroupRequest,
) -> BTreeMap<String, RuntimeParticipantBinding> {
    req.participant_bindings
        .iter()
        .map(|(binding_id, binding)| {
            (
                binding_id.clone(),
                RuntimeParticipantBinding {
                    source: binding.source.clone(),
                    bot_ids: binding.bot_ids.clone(),
                    extensions: Default::default(),
                },
            )
        })
        .collect()
}

fn inferred_participant_role_wire(bot_id: &str, driver_bot: &str) -> &'static str {
    if bot_id == driver_bot {
        "driver"
    } else {
        "consultant"
    }
}

fn build_group_chat_url(
    base: &str,
    group_id: &str,
    view_actor_id: &str,
    session_id: Option<&str>,
) -> String {
    let mut url = format!(
        "{}/bcn/chat/detail?id={}&bot_uuid={}",
        base.trim_end_matches('/'),
        urlencoding::encode(group_id),
        urlencoding::encode(view_actor_id),
    );
    if let Some(session_id) = session_id.filter(|value| !value.is_empty()) {
        url.push_str(&format!("&session={}", urlencoding::encode(session_id)));
    }
    url
}

fn group_detail_to_create_json(result: GroupDetailResult, created: bool) -> Value {
    serde_json::json!({
        "id": result.group_id,
        "context": result.context,
        "driver_bot": result.driver_bot_id,
        "participants": result.participants.iter().map(|p| &p.bot_uuid).collect::<Vec<_>>(),
        "context_injected": result.context_injected,
        "chat_url": result.chat_url,
        "session_id": result.latest_running_session_id,
        "group_kind": result.group_kind,
        "dm_pair_key": result.dm_pair_key,
        "created": created
    })
}

fn group_use_case_error_to_http(error: GroupUseCaseError) -> HttpAdapterError {
    match error {
        GroupUseCaseError::Unauthorized(message) => HttpAdapterError::Unauthorized(message),
        GroupUseCaseError::Forbidden(message) => HttpAdapterError::Forbidden(message),
        GroupUseCaseError::InvalidGroupId(message)
        | GroupUseCaseError::InvalidGroupStatus(message)
        | GroupUseCaseError::InvalidProposal(message) => HttpAdapterError::BadRequest(message),
        GroupUseCaseError::InvalidHistoryLimit(limit) => {
            HttpAdapterError::BadRequest(format!("Invalid history limit: {}", limit))
        }
        GroupUseCaseError::ActorNotFound(actor_id) => {
            HttpAdapterError::NotFound(format!("Actor '{}' not found", actor_id))
        }
        GroupUseCaseError::ProposalNotFound(proposal_id)
        | GroupUseCaseError::ProposalExpired(proposal_id) => {
            HttpAdapterError::NotFound(format!("Proposal '{}' not found or expired", proposal_id))
        }
        GroupUseCaseError::InvalidParticipantMode { mode, actor_kind } => {
            HttpAdapterError::BadRequest(format!(
                "mode '{:?}' is not valid for actor_kind '{:?}'",
                mode, actor_kind
            ))
        }
        GroupUseCaseError::Service(ServiceError::ExistNonPublicBots { bots }) => {
                let bot_list: Vec<Value> = bots
                    .iter()
                    .map(|(uuid, name)| {
                        serde_json::json!({
                            "bot_uuid": uuid,
                            "bot_name": name.as_deref().unwrap_or(uuid),
                        })
                    })
                    .collect();
                HttpAdapterError::BadRequestStructured {
                    message: "Group contains non-public bots preventing visibility change"
                        .into(),
                    params: serde_json::json!({
                        "code": "exist_none_public_bots",
                        "bots": bot_list,
                    }),
                }
            }
            GroupUseCaseError::Conflict(message) => HttpAdapterError::Conflict(message),
            GroupUseCaseError::Service(error) => HttpAdapterError::Service(error),
    }
}

fn collaboration_runtime_error_to_http(error: CollaborationRuntimeError) -> HttpAdapterError {
    match error {
        CollaborationRuntimeError::InvalidDefinition(message)
        | CollaborationRuntimeError::InvalidParticipantBinding(message)
        | CollaborationRuntimeError::InvalidRequest(message) => HttpAdapterError::BadRequest(message),
        CollaborationRuntimeError::DefinitionNotFound(id, version) => {
            HttpAdapterError::NotFound(format!(
                "CollaborationDefinition '{}@{}' not found",
                id, version
            ))
        }
        CollaborationRuntimeError::RunNotFound(run_id) => {
            HttpAdapterError::NotFound(format!("StateMachineRun '{}' not found", run_id))
        }
        CollaborationRuntimeError::Conflict(message) => HttpAdapterError::Conflict(message),
        CollaborationRuntimeError::Internal(error) => HttpAdapterError::Service(error),
    }
}

async fn resolve_put_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Result<String, Response> {
    if let Some(caller) = state.bot_uuid_from_headers(headers).await {
        return Ok(caller);
    }

    if let Some(staff_no) = state
        .user_identity
        .extract(headers, uri)
        .await
        .and_then(|identity| identity.staff_no)
        .filter(|staff_no| !staff_no.is_empty())
    {
        return Ok(format!("human_{}", staff_no));
    }

    Err((
        StatusCode::UNAUTHORIZED,
        Json(serde_json::json!({
            "success": false,
            "error": "Unauthorized: no valid token or login session"
        })),
    )
        .into_response())
}

async fn extract_human_actor_id(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Option<String> {
    state
        .user_identity
        .extract(headers, uri)
        .await
        .and_then(|identity| identity.staff_no)
        .filter(|staff_no| !staff_no.is_empty())
        .map(|staff_no| format!("human_{}", staff_no))
}

/// Resolve the caller bot identity for group member management endpoints.
///
/// Tries bot token first; falls back to human identity (cookie or
/// X-Mock-User-Id in local/dev mode) and looks up a bot owned by
/// the human that has coordinator access to the group.
async fn resolve_group_member_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
    group_id: &str,
) -> Result<String, HttpAdapterError> {
    if let Ok(bot_id) = authenticated_bot_from_headers(state, headers).await {
        return Ok(bot_id);
    }

    let human_id = extract_human_actor_id(state, headers, uri)
        .await
        .ok_or_else(|| HttpAdapterError::Unauthorized("valid bot token is required".to_string()))?;

    let group = state
        .services
        .group_query
        .get_group(GroupDetailCommand { group_id: group_id.to_string() })
        .await
        .map_err(group_use_case_error_to_http)?;

    let staff_no = human_id.strip_prefix("human_").unwrap_or(&human_id);

    let owned = state
        .services
        .bot_query
        .list_bots_by_creator(staff_no)
        .await
        .map_err(bot_use_case_error_to_http)?;
    let coordinator = owned.iter().find(|b| {
        b.bot_uuid == group.driver_bot_id || group.originator.as_deref() == Some(&b.bot_uuid)
    });

    coordinator
        .map(|b| b.bot_uuid.clone())
        .ok_or_else(|| {
            HttpAdapterError::Forbidden(
                "human caller does not own a coordinator bot in this group".to_string(),
            )
        })
}

async fn resolve_actor_caller(
    state: &HttpAppState,
    headers: &HeaderMap,
    uri: &Uri,
) -> Result<String, HttpAdapterError> {
    if let Ok(bot_id) = authenticated_bot_from_headers(state, headers).await {
        return Ok(bot_id);
    }
    extract_human_actor_id(state, headers, uri)
        .await
        .ok_or_else(|| HttpAdapterError::Unauthorized("valid bot token or human cookie is required".to_string()))
}

fn group_use_case_error_to_mode_response(error: GroupUseCaseError) -> (StatusCode, Json<Value>) {
    match error {
        GroupUseCaseError::Unauthorized(message) => (
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({
                "success": false,
                "error": message
            })),
        ),
        GroupUseCaseError::Forbidden(message) => (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({
                "success": false,
                "error": message
            })),
        ),
        GroupUseCaseError::ActorNotFound(actor_id) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({
                "success": false,
                "error": format!("Actor '{}' not found", actor_id)
            })),
        ),
        GroupUseCaseError::ProposalNotFound(proposal_id)
        | GroupUseCaseError::ProposalExpired(proposal_id) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({
                "success": false,
                "error": format!("Proposal '{}' not found or expired", proposal_id)
            })),
        ),
        GroupUseCaseError::InvalidParticipantMode { mode, actor_kind } => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "success": false,
                "error": format!(
                    "mode '{:?}' is not valid for actor_kind '{:?}'",
                    mode, actor_kind
                )
            })),
        ),
        GroupUseCaseError::InvalidGroupId(message)
        | GroupUseCaseError::InvalidGroupStatus(message)
        | GroupUseCaseError::InvalidProposal(message) => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "success": false,
                "error": message
            })),
        ),
        GroupUseCaseError::InvalidHistoryLimit(limit) => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "success": false,
                "error": format!("Invalid history limit: {}", limit)
            })),
        ),
        GroupUseCaseError::Conflict(message) => (
            StatusCode::CONFLICT,
            Json(serde_json::json!({
                "success": false,
                "error": message
            })),
        ),
        GroupUseCaseError::Service(error) => service_error_to_mode_response(error),
    }
}

fn service_error_to_mode_response(error: ServiceError) -> (StatusCode, Json<Value>) {
    let (status, message) = match &error {
        ServiceError::CannotAddSelf => (StatusCode::BAD_REQUEST, error.to_string()),
        ServiceError::PendingRequestExists { .. } => (StatusCode::CONFLICT, error.to_string()),
        ServiceError::BotNotFound(_) => (StatusCode::NOT_FOUND, error.to_string()),
        ServiceError::BotNotRegistered(_) => (StatusCode::NOT_FOUND, error.to_string()),
        ServiceError::FriendRequestNotFound(_) => (StatusCode::NOT_FOUND, error.to_string()),
        ServiceError::NotFriends(_) => (StatusCode::FORBIDDEN, error.to_string()),
        ServiceError::Conflict(_) => (StatusCode::CONFLICT, error.to_string()),
        ServiceError::InvalidOperation { .. } => (StatusCode::CONFLICT, error.to_string()),
        ServiceError::CannotAcceptRejected => (StatusCode::CONFLICT, error.to_string()),
        ServiceError::CannotRejectAccepted => (StatusCode::CONFLICT, error.to_string()),
        ServiceError::PrivateBotCannotCollaborate => (StatusCode::FORBIDDEN, error.to_string()),
        _ => (StatusCode::INTERNAL_SERVER_ERROR, error.to_string()),
    };
    (
        status,
        Json(serde_json::json!({
            "success": false,
            "error": message
        })),
    )
}

fn routing_policy_from_protocol_value(
    routing_policy: Option<Value>,
) -> Result<Option<RoutingPolicy>, HttpAdapterError> {
    match routing_policy {
        Some(value) => serde_json::from_value(value)
            .map(Some)
            .map_err(|e| HttpAdapterError::BadRequest(format!("invalid routing_policy: {}", e))),
        None => Ok(None),
    }
}

fn group_kind_from_str(value: &str) -> Result<GroupKind, HttpAdapterError> {
    match value {
        "normal" => Ok(GroupKind::Normal),
        "dm" => Ok(GroupKind::Dm),
        other => Err(HttpAdapterError::BadRequest(format!(
            "invalid group_kind: '{}'",
            other
        ))),
    }
}

fn group_kind_filter(filter: GroupKindFilter) -> Option<GroupKind> {
    match filter {
        GroupKindFilter::Normal => Some(GroupKind::Normal),
        GroupKindFilter::Dm => Some(GroupKind::Dm),
        GroupKindFilter::All => None,
    }
}

fn group_list_entry_to_legacy_json(group: GroupListEntry) -> Value {
    let driver_bot_name = if group.driver_bot_id.is_empty() {
        None
    } else {
        group.participants.iter()
            .find(|p| p.bot_uuid == group.driver_bot_id)
            .and_then(|p| p.bot_name.clone())
    };
    let originator_name = group.originator.as_ref()
        .filter(|id| !id.is_empty())
        .and_then(|id| {
            group.participants.iter()
                .find(|p| p.bot_uuid == *id)
                .and_then(|p| p.bot_name.clone())
        });

    serde_json::json!({
        "id": group.group_id,
        "label": group.label,
        "context": group.context,
        "driver_bot": group.driver_bot_id,
        "driver_bot_name": driver_bot_name,
        "originator": group.originator,
        "originator_name": originator_name,
        "participant_count": group.participant_count,
        "message_count": group.message_count,
        "created_at": group.created_at,
        "updated_at": group.updated_at,
        "group_kind": group.group_kind,
        "group_strategy": group.group_strategy,
        "visibility": group.visibility,
    })
}

fn bot_group_list_entry_to_legacy_json(group: GroupListEntry) -> Value {
    serde_json::json!({
        "group_id": group.group_id,
        "label": group.label,
        "coordinator_bot": group.driver_bot_id,
        "participants": group.participants,
        "created_at": group.created_at,
        "updated_at": group.updated_at,
        "group_kind": group.group_kind,
        "group_strategy": group.group_strategy,
        "visibility": group.visibility,
    })
}

fn group_to_detail_json(group: GroupDetailResult) -> Value {
    serde_json::json!({
        "id": group.group_id,
        "label": group.label,
        "status": group.status,
        "context": group.context,
        "driver_bot": group.driver_bot_id,
        "participants": group.participants,
        "message_count": group.message_count,
        "workspace": group.workspace,
        "service_group_uuid": group.service_group_uuid,
        "service_mode": group.service_mode,
        "created_at": group.created_at,
        "updated_at": group.updated_at,
        "group_kind": group.group_kind,
        "dm_pair_key": group.dm_pair_key,
        "group_strategy": group.group_strategy,
        "service_spec": group.service_spec,
        "latest_running_session_id": group.latest_running_session_id,
        "originator": group.originator,
        "visibility": group.visibility,
    })
}

async fn resolve_driver_bot_owner(
    state: &HttpAppState,
    driver_bot_id: &str,
) -> (Option<String>, Option<String>) {
    let driver = match state
        .services
        .bot_query
        .get_bot(BotDetailCommand {
            caller_actor_id: None,
            bot_id: driver_bot_id.to_string(),
        })
        .await
    {
        Ok(bot) => bot,
        Err(_) => return (None, None),
    };
    let staff_no = match driver.created_by {
        Some(s) if !s.is_empty() => s,
        _ => return (None, None),
    };
    let human_id = format!("human_{}", staff_no);
    let owner_name = state
        .services
        .bot_query
        .get_bot(BotDetailCommand {
            caller_actor_id: None,
            bot_id: human_id.clone(),
        })
        .await
        .ok()
        .and_then(|h| h.capabilities.name);
    (Some(human_id), owner_name)
}

fn default_member_role() -> String {
    "consultant".to_string()
}

fn group_status_to_wire(status: GroupStatus) -> &'static str {
    match status {
        GroupStatus::Active => "active",
        GroupStatus::Completed => "completed",
        GroupStatus::Error => "error",
        GroupStatus::Closed => "closed",
        GroupStatus::Inactive => "inactive",
    }
}

fn validate_service_spec_callback_urls(
    guard: &OutboundUrlGuard,
    service_spec: Option<&ServiceSpec>,
) -> Result<(), String> {
    let Some(callback_config) = service_spec.and_then(|spec| spec.callback_config.as_ref()) else {
        return Ok(());
    };
    for (index, channel) in callback_config.channels.iter().enumerate() {
        if let CallbackChannelConfig::Baas { base_url, .. } = channel {
            guard
                .validate_configured_http_url(base_url)
                .map_err(|error| {
                    format!(
                        "service_spec.callback_config.channels[{index}].base_url is not allowed: {error}"
                    )
                })?;
        }
    }
    Ok(())
}

// ---------------------------------------------------------------
// PATCH /groups/{id}/settings
// ---------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct PatchGroupSettingsRequest {
    #[serde(default)]
    pub service_spec: Option<Option<ServiceSpec>>,
}

pub async fn patch_group_settings(
    State(state): State<HttpAppState>,
    Path(group_id): Path<String>,
    Json(body): Json<PatchGroupSettingsRequest>,
) -> impl IntoResponse {
    // Security: block callback URLs that resolve to private/local addresses
    // before delegating to the use case. The app service owns the
    // immutability / route-field-lock validation; the outbound URL guard is an
    // adapter-level network policy and lives at the route boundary.
    if let Some(ref spec_patch) = body.service_spec {
        if let Err(e) = validate_service_spec_callback_urls(
            &state.outbound_url_guard,
            spec_patch.as_ref(),
        ) {
            return (StatusCode::BAD_REQUEST, Json(serde_json::json!({"error": e})))
                .into_response();
        }
    }

    match state
        .services
        .group_management
        .patch_group_settings(GroupPatchSettingsCommand {
            group_id: group_id.clone(),
            service_spec: body.service_spec,
        })
        .await
    {
        Ok(result) => (
            StatusCode::OK,
            Json(serde_json::json!({
                "id": result.group_id,
                "service_spec": result.service_spec,
                "status": "ok",
            })),
        )
            .into_response(),
        Err(GroupUseCaseError::Conflict(message)) => {
            // The use case JSON-encodes a `GroupPatchSettingsConflict` into
            // the message; surface it verbatim so clients recover the detail.
            let conflict: Value =
                serde_json::from_str(&message).unwrap_or_else(|_| serde_json::json!({"error": message}));
            (StatusCode::CONFLICT, Json(conflict)).into_response()
        }
        Err(GroupUseCaseError::Service(ServiceError::GroupNotFound(_))) => {
            (StatusCode::NOT_FOUND, Json(serde_json::json!({"error": "group not found"})))
                .into_response()
        }
        Err(error) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"error": error.to_string()})),
        )
            .into_response(),
    }
}
